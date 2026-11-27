import hashlib
import hmac
import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.auth import current_user
from app.core.config import settings
from app.core.secret_store import SecretStoreUnavailable, decrypt_secret, encrypt_secret
from app.db.session import get_db
from app.models.entities import (
    AuditEvent,
    Integration,
    Project,
    TelegramChat,
    TelegramMessage,
    User,
    WorkspaceMember,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/integrations/telegram", tags=["Telegram integration"]
)
public_router = APIRouter(tags=["Telegram webhooks"])
logger = logging.getLogger("sahakarya.telegram")


class TelegramConnectRequest(BaseModel):
    bot_token: SecretStr = Field(min_length=20, max_length=512)

    @field_validator("bot_token")
    @classmethod
    def token_has_no_controls(cls, value: SecretStr) -> SecretStr:
        if any(ord(char) < 32 or ord(char) == 127 for char in value.get_secret_value()):
            raise ValueError("Bot token contains unsupported control characters")
        return value


class TelegramConnectView(BaseModel):
    integration_id: UUID
    status: str
    bot_username: str | None
    bot_name: str | None
    last_checked_at: datetime | None
    last_error: str | None = None


class TelegramChatView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    chat_type: str
    username: str | None
    project_id: UUID | None
    status: str
    last_message_at: datetime | None
    created_at: datetime


class TelegramChatUpdate(BaseModel):
    project_id: UUID | None = None
    enabled: bool


@router.get("/status", response_model=TelegramConnectView)
def telegram_status(
    workspace_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TelegramConnectView:
    _require_workspace(db, workspace_id, user.id, write=False)
    integration = db.scalar(
        select(Integration).where(
            Integration.workspace_id == workspace_id,
            Integration.provider == "telegram",
        )
    )
    if integration is None:
        return TelegramConnectView(
            integration_id=UUID(int=0),
            status="not_configured",
            bot_username=None,
            bot_name=None,
            last_checked_at=None,
            last_error=None,
        )
    return TelegramConnectView(
        integration_id=integration.id,
        status=integration.status,
        bot_username=integration.config_json.get("bot_username"),
        bot_name=integration.config_json.get("bot_name"),
        last_checked_at=integration.last_checked_at,
        last_error=integration.last_error,
    )


def _require_workspace(db: Session, workspace_id: UUID, user_id: UUID, *, write: bool) -> None:
    role = db.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if write and role == "viewer":
        raise HTTPException(status_code=403, detail="Viewer access is read-only")


async def _telegram_api(token: str, method: str, payload: dict[str, Any] | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx2.AsyncClient(timeout=httpx2.Timeout(10.0, read=20.0)) as client:
        response = await client.post(url, json=payload or {})
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise ValueError("Telegram API rejected the request")
    result = data.get("result")
    return result if isinstance(result, dict) else {}


@router.post("/connect", response_model=TelegramConnectView)
async def connect_telegram(
    workspace_id: UUID,
    payload: TelegramConnectRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TelegramConnectView:
    _require_workspace(db, workspace_id, user.id, write=True)
    if not settings.integration_encryption_key:
        raise HTTPException(status_code=503, detail="Configure INTEGRATION_ENCRYPTION_KEY first.")
    if not settings.public_api_url or not settings.public_api_url.startswith("https://"):
        raise HTTPException(status_code=503, detail="Configure the public HTTPS API URL first.")

    token = payload.bot_token.get_secret_value()
    try:
        bot = await _telegram_api(token, "getMe")
    except Exception as exc:
        logger.warning("Telegram bot validation failed error_type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=422, detail="Telegram could not validate this bot token."
        ) from exc
    if bot.get("is_bot") is not True or not isinstance(bot.get("id"), int):
        raise HTTPException(status_code=422, detail="Telegram token did not identify a bot.")

    integration = db.scalar(
        select(Integration).where(
            Integration.workspace_id == workspace_id,
            Integration.provider == "telegram",
        )
    )
    if integration is None:
        integration = Integration(
            workspace_id=workspace_id,
            provider="telegram",
            display_name="Telegram",
            status="connecting",
            config_json={},
            capabilities_json=[],
        )
        db.add(integration)
        db.flush()

    webhook_secret = secrets.token_urlsafe(32)
    try:
        encrypted = encrypt_secret(token)
    except SecretStoreUnavailable as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    webhook_url = (
        f"{settings.public_api_url.rstrip('/')}{settings.api_v1_prefix}"
        f"/integrations/telegram/webhook/{integration.id}"
    )
    try:
        await _telegram_api(
            token,
            "setWebhook",
            {
                "url": webhook_url,
                "secret_token": webhook_secret,
                "allowed_updates": ["message"],
                "drop_pending_updates": True,
            },
        )
    except Exception as exc:
        logger.warning("Telegram webhook registration failed error_type=%s", type(exc).__name__)
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail="Bot token was validated, but Telegram could not register the webhook.",
        ) from exc

    integration.secret_ciphertext = encrypted
    integration.secret_ref = "fernet-v1"
    integration.status = "connected"
    integration.last_error = None
    integration.last_checked_at = datetime.now(UTC)
    integration.config_json = {
        "endpoint": "https://api.telegram.org",
        "bot_id": bot["id"],
        "bot_username": bot.get("username"),
        "bot_name": bot.get("first_name"),
        "webhook_secret_hash": hashlib.sha256(webhook_secret.encode()).hexdigest(),
        "webhook_url": webhook_url,
        "protocol": "telegram_bot_api_webhook",
    }
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="integration.telegram_connected",
            object_type="integration",
            object_id=integration.id,
            details_json={"provider": "telegram", "bot_username": bot.get("username")},
        )
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        try:
            await _telegram_api(token, "deleteWebhook", {"drop_pending_updates": True})
        except Exception as cleanup_error:
            logger.warning(
                "Telegram webhook cleanup failed error_type=%s", type(cleanup_error).__name__
            )
        raise
    db.refresh(integration)
    return TelegramConnectView(
        integration_id=integration.id,
        status=integration.status,
        bot_username=bot.get("username"),
        bot_name=bot.get("first_name"),
        last_checked_at=integration.last_checked_at,
        last_error=integration.last_error,
    )


@router.get("/chats", response_model=list[TelegramChatView])
def list_telegram_chats(
    workspace_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[TelegramChatView]:
    _require_workspace(db, workspace_id, user.id, write=False)
    return list(
        db.scalars(
            select(TelegramChat)
            .where(TelegramChat.workspace_id == workspace_id)
            .order_by(TelegramChat.created_at.desc())
        ).all()
    )


@router.patch("/chats/{telegram_chat_id}", response_model=TelegramChatView)
def update_telegram_chat(
    workspace_id: UUID,
    telegram_chat_id: UUID,
    payload: TelegramChatUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TelegramChatView:
    _require_workspace(db, workspace_id, user.id, write=True)
    chat = db.scalar(
        select(TelegramChat).where(
            TelegramChat.workspace_id == workspace_id,
            TelegramChat.id == telegram_chat_id,
        )
    )
    if chat is None:
        raise HTTPException(status_code=404, detail="Telegram group not found")
    if payload.project_id is not None:
        project = db.scalar(
            select(Project.id).where(
                Project.workspace_id == workspace_id, Project.id == payload.project_id
            )
        )
        if project is None:
            raise HTTPException(status_code=422, detail="Project is outside this workspace")
    chat.project_id = payload.project_id
    chat.status = "connected" if payload.enabled else "paused"
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="integration.telegram_group_updated",
            object_type="telegram_chat",
            object_id=chat.id,
            details_json={
                "status": chat.status,
                "project_id": str(chat.project_id) if chat.project_id else None,
            },
        )
    )
    db.commit()
    db.refresh(chat)
    return TelegramChatView.model_validate(chat)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_telegram(
    workspace_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    _require_workspace(db, workspace_id, user.id, write=True)
    integration = db.scalar(
        select(Integration).where(
            Integration.workspace_id == workspace_id,
            Integration.provider == "telegram",
        )
    )
    if integration is None:
        return Response(status_code=204)
    try:
        token = decrypt_secret(integration.secret_ciphertext or "")
        await _telegram_api(token, "deleteWebhook", {"drop_pending_updates": True})
    except Exception as exc:
        logger.warning("Telegram webhook removal failed error_type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="Telegram could not confirm webhook removal. The connection was kept.",
        ) from exc
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="integration.disconnected",
            object_type="integration",
            object_id=integration.id,
            details_json={"provider": "telegram"},
        )
    )
    integration.status = "not_configured"
    integration.secret_ciphertext = None
    integration.secret_ref = None
    integration.config_json = {
        key: value
        for key, value in integration.config_json.items()
        if key not in {"webhook_secret_hash", "webhook_url"}
    }
    integration.last_checked_at = datetime.now(UTC)
    for chat in db.scalars(
        select(TelegramChat).where(TelegramChat.integration_id == integration.id)
    ):
        chat.status = "paused"
    db.commit()
    return Response(status_code=204)


@public_router.post("/integrations/telegram/webhook/{integration_id}")
async def telegram_webhook(
    integration_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    integration = db.get(Integration, integration_id)
    if (
        integration is None
        or integration.provider != "telegram"
        or integration.status != "connected"
    ):
        raise HTTPException(status_code=404, detail="Telegram integration not found")
    supplied = request.headers.get("x-telegram-bot-api-secret-token", "")
    expected_hash = integration.config_json.get("webhook_secret_hash", "")
    if not supplied or not hmac.compare_digest(
        hashlib.sha256(supplied.encode()).hexdigest(), expected_hash
    ):
        raise HTTPException(status_code=403, detail="Webhook verification failed")
    if (
        request.headers.get("content-length", "0").isdigit()
        and int(request.headers["content-length"]) > 1_048_576
    ):
        raise HTTPException(status_code=413, detail="Telegram update is too large")
    try:
        body = await request.body()
        if len(body) > 1_048_576:
            raise HTTPException(status_code=413, detail="Telegram update is too large")
        update = json.loads(body)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Telegram update") from exc
    if not isinstance(update, dict):
        raise HTTPException(status_code=400, detail="Invalid Telegram update")
    message = update.get("message")
    if not isinstance(message, dict):
        return {"ok": True}
    chat_data = message.get("chat")
    if not isinstance(chat_data, dict) or chat_data.get("type") not in {"group", "supergroup"}:
        return {"ok": True}
    telegram_chat_id = chat_data.get("id")
    telegram_message_id = message.get("message_id")
    if not isinstance(telegram_chat_id, int) or not isinstance(telegram_message_id, int):
        return {"ok": True}

    chat = db.scalar(
        select(TelegramChat).where(
            TelegramChat.integration_id == integration.id,
            TelegramChat.telegram_chat_id == telegram_chat_id,
        )
    )
    title = str(chat_data.get("title") or chat_data.get("username") or "Telegram group")[:255]
    if chat is None:
        chat = TelegramChat(
            workspace_id=integration.workspace_id,
            integration_id=integration.id,
            telegram_chat_id=telegram_chat_id,
            title=title,
            chat_type=str(chat_data.get("type"))[:32],
            username=str(chat_data["username"])[:255] if chat_data.get("username") else None,
            status="pending",
        )
        db.add(chat)
        db.flush()
    else:
        chat.title = title
        if chat_data.get("username"):
            chat.username = str(chat_data["username"])[:255]

    content = message.get("text") or message.get("caption")
    if chat.status == "connected" and isinstance(content, str) and content.strip():
        exists = db.scalar(
            select(TelegramMessage.id).where(
                TelegramMessage.chat_id == chat.id,
                TelegramMessage.telegram_message_id == telegram_message_id,
            )
        )
        if exists is None:
            sender = message.get("from") if isinstance(message.get("from"), dict) else {}
            sender_name = " ".join(
                part
                for part in (sender.get("first_name"), sender.get("last_name"))
                if isinstance(part, str) and part.strip()
            ) or (f"@{sender['username']}" if sender.get("username") else "Telegram member")
            message_date = message.get("date")
            try:
                occurred_at = datetime.fromtimestamp(int(message_date), UTC)
            except (TypeError, ValueError, OSError, OverflowError):
                occurred_at = datetime.now(UTC)
            source_url = (
                f"https://t.me/{chat.username}/{telegram_message_id}" if chat.username else None
            )
            telegram_message = TelegramMessage(
                workspace_id=chat.workspace_id,
                chat_id=chat.id,
                project_id=chat.project_id,
                telegram_message_id=telegram_message_id,
                sender_telegram_id=sender.get("id") if isinstance(sender.get("id"), int) else None,
                sender_name=sender_name[:255],
                content=content.strip()[:5000],
                message_at=occurred_at,
                source_url=source_url,
            )
            db.add(telegram_message)
            chat.last_message_at = occurred_at
            db.add(
                AuditEvent(
                    workspace_id=chat.workspace_id,
                    event_type="integration.telegram_message_received",
                    object_type="telegram_message",
                    object_id=telegram_message.id,
                    details_json={
                        "chat_title": chat.title,
                        "telegram_message_id": telegram_message_id,
                    },
                )
            )
    db.commit()
    return {"ok": True}
