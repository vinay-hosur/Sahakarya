import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Response, status
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from app.api.v1.auth import current_user
from app.core.config import settings
from app.core.secret_store import SecretStoreUnavailable, decrypt_secret, encrypt_secret
from app.db.session import get_db
from app.models.entities import (
    AuditEvent,
    Integration,
    IntegrationOAuthState,
    User,
    WorkspaceMember,
)

logger = logging.getLogger("sahakarya.integrations")
router = APIRouter(prefix="/workspaces/{workspace_id}/integrations", tags=["integrations"])
oauth_router = APIRouter(prefix="/integrations/slack/oauth", tags=["integrations"])

Provider = Literal["github", "slack"]
PROVIDERS: dict[Provider, dict[str, str]] = {
    "github": {
        "display_name": "GitHub",
        "endpoint": "https://api.githubcopilot.com/mcp/",
    },
    "slack": {"display_name": "Slack", "endpoint": "https://mcp.slack.com/mcp"},
}


class ConnectRequest(BaseModel):
    access_token: SecretStr | None = Field(default=None, min_length=10, max_length=4096)

    @field_validator("access_token")
    @classmethod
    def token_must_not_contain_controls(cls, value: SecretStr | None) -> SecretStr | None:
        if value and any(ord(char) < 32 or ord(char) == 127 for char in value.get_secret_value()):
            raise ValueError("Access token contains unsupported control characters")
        return value


class IntegrationView(BaseModel):
    id: UUID
    provider: str
    display_name: str
    endpoint: str
    status: str
    last_checked_at: datetime | None
    last_error: str | None
    capabilities: list[dict]
    credential_configured: bool


async def discover_mcp(
    endpoint: str, access_token: str
) -> tuple[str | None, dict | None, list[dict]]:
    headers = {"Authorization": f"Bearer {access_token}"}
    timeout = httpx2.Timeout(20.0, read=45.0)
    async with httpx2.AsyncClient(headers=headers, timeout=timeout) as http_client:
        transport = streamable_http_client(endpoint, http_client=http_client)
        async with Client(transport, read_timeout_seconds=45.0) as client:
            protocol_version = client.protocol_version
            server_info = (
                client.server_info.model_dump(mode="json", exclude_none=True)
                if client.server_info
                else None
            )
            capabilities: list[dict] = []
            cursor: str | None = None
            for _ in range(10):
                page = await client.list_tools(cursor=cursor)
                for tool in page.tools:
                    capabilities.append(
                        tool.model_dump(mode="json", exclude_none=True, by_alias=True)
                    )
                    if len(capabilities) >= 100:
                        break
                if len(capabilities) >= 100 or not page.next_cursor:
                    break
                cursor = page.next_cursor
            return protocol_version, server_info, capabilities


def _view(integration: Integration) -> IntegrationView:
    return IntegrationView(
        id=integration.id,
        provider=integration.provider,
        display_name=integration.display_name,
        endpoint=integration.config_json.get("endpoint", ""),
        status=integration.status,
        last_checked_at=integration.last_checked_at,
        last_error=integration.last_error,
        capabilities=integration.capabilities_json,
        credential_configured=bool(integration.secret_ciphertext),
    )


def _require_workspace(
    db: Session, workspace_id: UUID, user_id: UUID, *, write: bool = False
) -> str:
    role = db.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    if write and role == "viewer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Viewer access is read-only"
        )
    return role


@router.get("", response_model=list[IntegrationView])
def list_integrations(
    workspace_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[IntegrationView]:
    _require_workspace(db, workspace_id, user.id)
    integrations = db.scalars(
        select(Integration)
        .where(Integration.workspace_id == workspace_id)
        .order_by(Integration.provider)
    ).all()
    return [_view(integration) for integration in integrations]


@router.post("/{provider}/connect", response_model=IntegrationView)
async def connect_integration(
    workspace_id: UUID,
    provider: Provider,
    payload: ConnectRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> IntegrationView:
    _require_workspace(db, workspace_id, user.id, write=True)
    if provider == "slack":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Connect Slack through the registered-app OAuth flow.",
        )
    provider_config = PROVIDERS[provider]
    integration = db.scalar(
        select(Integration).where(
            Integration.workspace_id == workspace_id,
            Integration.provider == provider,
        )
    )

    token = payload.access_token.get_secret_value() if payload.access_token else None
    if token:
        try:
            encrypted_token = encrypt_secret(token)
        except SecretStoreUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    elif integration and integration.secret_ciphertext:
        try:
            token = decrypt_secret(integration.secret_ciphertext)
            encrypted_token = integration.secret_ciphertext
        except SecretStoreUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    else:
        raise HTTPException(
            status_code=422, detail="Enter an access token to connect this provider"
        )

    if integration is None:
        integration = Integration(
            workspace_id=workspace_id,
            provider=provider,
            display_name=provider_config["display_name"],
            status="connecting",
            config_json={"endpoint": provider_config["endpoint"], "protocol": "streamable_http"},
            capabilities_json=[],
        )
        db.add(integration)
    integration.secret_ciphertext = encrypted_token
    integration.secret_ref = "fernet-v1"
    integration.status = "connecting"
    integration.last_error = None
    db.commit()
    db.refresh(integration)

    try:
        protocol_version, server_info, capabilities = await discover_mcp(
            provider_config["endpoint"], token
        )
        integration.status = "connected"
        integration.last_error = None
        integration.last_checked_at = datetime.now(UTC)
        integration.capabilities_json = capabilities
        integration.config_json = {
            "endpoint": provider_config["endpoint"],
            "protocol": "streamable_http",
            "protocol_version": protocol_version,
            "server_info": server_info,
        }
        audit_details = {
            "provider": provider,
            "status": "connected",
            "protocol_version": protocol_version,
            "tool_count": len(capabilities),
            "tool_names": [tool.get("name", "") for tool in capabilities],
        }
    except Exception as exc:
        logger.warning(
            "MCP verification failed provider=%s error_type=%s", provider, type(exc).__name__
        )
        integration.status = "error"
        integration.last_error = "MCP connection or capability discovery failed"
        integration.last_checked_at = datetime.now(UTC)
        integration.capabilities_json = []
        audit_details = {"provider": provider, "status": "error", "error_type": type(exc).__name__}
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="integration.connection_checked",
            object_type="integration",
            object_id=integration.id,
            details_json=audit_details,
        )
    )
    db.commit()
    db.refresh(integration)

    if integration.status != "connected":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=integration.last_error,
        )
    return _view(integration)


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_integration(
    workspace_id: UUID,
    provider: Provider,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    _require_workspace(db, workspace_id, user.id, write=True)
    integration = db.scalar(
        select(Integration).where(
            Integration.workspace_id == workspace_id,
            Integration.provider == provider,
        )
    )
    if integration is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if provider == "slack" and integration.secret_ciphertext:
        try:
            token = decrypt_secret(integration.secret_ciphertext)
            async with httpx2.AsyncClient(timeout=httpx2.Timeout(10.0, read=20.0)) as client:
                response = await client.post(
                    "https://slack.com/api/auth.revoke",
                    headers={"Authorization": f"Bearer {token}"},
                )
            response.raise_for_status()
            if response.json().get("ok") is not True:
                raise ValueError("Slack did not revoke the user token")
        except Exception as exc:
            logger.warning("Slack token revocation failed error_type=%s", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Slack could not confirm token revocation. The connection was kept.",
            ) from exc

    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="integration.disconnected",
            object_type="integration",
            object_id=integration.id,
            details_json={"provider": provider},
        )
    )
    db.delete(integration)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/slack/oauth/start")
def start_slack_oauth(
    workspace_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    _require_workspace(db, workspace_id, user.id, write=True)
    if not settings.slack_oauth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Configure Slack client ID, client secret, and HTTPS callback URL first.",
        )
    if not settings.integration_encryption_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Configure INTEGRATION_ENCRYPTION_KEY before connecting Slack.",
        )
    scopes = [scope.strip() for scope in settings.slack_user_scopes.split(",") if scope.strip()]
    if not scopes:
        raise HTTPException(status_code=503, detail="Configure at least one Slack user scope.")

    now = datetime.now(UTC)
    db.execute(delete(IntegrationOAuthState).where(IntegrationOAuthState.expires_at <= now))
    state = secrets.token_urlsafe(32)
    db.add(
        IntegrationOAuthState(
            provider="slack",
            workspace_id=workspace_id,
            user_id=user.id,
            state_hash=hashlib.sha256(state.encode("utf-8")).hexdigest(),
            redirect_uri=settings.slack_callback_uri,
            expires_at=now + timedelta(minutes=10),
        )
    )
    db.commit()
    query = urlencode(
        {
            "client_id": settings.slack_client_id,
            "scope": ",".join(scopes),
            "redirect_uri": settings.slack_callback_uri,
            "state": state,
        }
    )
    return {"authorization_url": f"https://slack.com/oauth/v2_user/authorize?{query}"}


def _slack_result_redirect(result: str) -> RedirectResponse:
    base_url = settings.slack_frontend_url.rstrip("/")
    return RedirectResponse(
        url=f"{base_url}/?slack_oauth={result}#integrations",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@oauth_router.get("/callback")
async def slack_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not state:
        return _slack_result_redirect("invalid_state")
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    oauth_state = db.scalar(
        select(IntegrationOAuthState).where(
            IntegrationOAuthState.provider == "slack",
            IntegrationOAuthState.state_hash == state_hash,
        )
    )
    if oauth_state is None:
        return _slack_result_redirect("invalid_state")
    expires_at = oauth_state.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        db.delete(oauth_state)
        db.commit()
        return _slack_result_redirect("expired_state")
    workspace_id, user_id, redirect_uri = (
        oauth_state.workspace_id,
        oauth_state.user_id,
        oauth_state.redirect_uri,
    )
    db.delete(oauth_state)
    db.commit()
    if error:
        return _slack_result_redirect("cancelled")
    if not code or not settings.slack_oauth_configured:
        return _slack_result_redirect("configuration_error")
    role = db.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if role not in {"owner", "member"}:
        return _slack_result_redirect("access_changed")

    try:
        async with httpx2.AsyncClient(
            timeout=httpx2.Timeout(10.0, read=30.0), follow_redirects=False
        ) as client:
            response = await client.post(
                "https://slack.com/api/oauth.v2.user.access",
                data={"code": code, "redirect_uri": redirect_uri},
                auth=(
                    settings.slack_client_id or "",
                    settings.slack_client_secret.get_secret_value()
                    if settings.slack_client_secret
                    else "",
                ),
            )
        response.raise_for_status()
        token_payload = response.json()
        token = token_payload.get("access_token")
        if token_payload.get("ok") is not True or not isinstance(token, str) or not token:
            raise ValueError("Slack did not issue a user token")
        encrypted_token = encrypt_secret(token)
        protocol_version, server_info, capabilities = await discover_mcp(
            PROVIDERS["slack"]["endpoint"], token
        )
    except Exception as exc:
        logger.warning("Slack OAuth verification failed error_type=%s", type(exc).__name__)
        return _slack_result_redirect("connect_error")

    integration = db.scalar(
        select(Integration).where(
            Integration.workspace_id == workspace_id,
            Integration.provider == "slack",
        )
    )
    if integration is None:
        integration = Integration(
            workspace_id=workspace_id,
            provider="slack",
            display_name=PROVIDERS["slack"]["display_name"],
            status="connecting",
            config_json={},
            capabilities_json=[],
        )
        db.add(integration)
        db.flush()
    team = token_payload.get("team")
    team = team if isinstance(team, dict) else {}
    authed_user = token_payload.get("authed_user")
    authed_user = authed_user if isinstance(authed_user, dict) else {}
    integration.secret_ciphertext = encrypted_token
    integration.secret_ref = "fernet-v1"
    integration.status = "connected"
    integration.last_error = None
    integration.last_checked_at = datetime.now(UTC)
    integration.capabilities_json = capabilities
    integration.config_json = {
        "endpoint": PROVIDERS["slack"]["endpoint"],
        "protocol": "streamable_http",
        "protocol_version": protocol_version,
        "server_info": server_info,
        "team_id": team.get("id"),
        "team_name": team.get("name"),
        "user_id": authed_user.get("id"),
        "scopes": token_payload.get("scope", ""),
    }
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            event_type="integration.connection_checked",
            object_type="integration",
            object_id=integration.id,
            details_json={
                "provider": "slack",
                "status": "connected",
                "protocol_version": protocol_version,
                "tool_count": len(capabilities),
                "tool_names": [tool.get("name", "") for tool in capabilities],
                "team_id": team.get("id"),
            },
        )
    )
    db.commit()
    return _slack_result_redirect("connected")
