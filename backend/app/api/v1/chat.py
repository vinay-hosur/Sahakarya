import asyncio
import hashlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.auth import current_user
from app.core.config import settings
from app.db.session import SessionLocal, get_db
from app.models.entities import (
    AuditEvent,
    AuthSession,
    ChatChannel,
    ChatMessage,
    User,
    WorkspaceMember,
)
from app.services.notifications import notify_mentions

router = APIRouter(prefix="/workspaces/{workspace_id}/chat", tags=["workspace chat"])


class ChannelCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip().lstrip("#")
        if len(value) < 2:
            raise ValueError("Channel name must contain at least two characters")
        return value


class ChannelView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    name: str
    created_by: UUID
    created_at: datetime
    last_activity_at: datetime


class MessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)

    @field_validator("body")
    @classmethod
    def normalize_body(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be blank")
        return value


class MessageView(BaseModel):
    id: UUID
    workspace_id: UUID
    channel_id: UUID
    sender_user_id: UUID
    sender_display_name: str
    body: str
    created_at: datetime


class RoomHub:
    def __init__(self) -> None:
        self._rooms: dict[tuple[UUID, UUID], set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def join(self, room: tuple[UUID, UUID], socket: WebSocket) -> None:
        async with self._lock:
            self._rooms.setdefault(room, set()).add(socket)

    async def leave(self, room: tuple[UUID, UUID], socket: WebSocket) -> None:
        async with self._lock:
            members = self._rooms.get(room)
            if members:
                members.discard(socket)
                if not members:
                    self._rooms.pop(room, None)

    async def publish(self, room: tuple[UUID, UUID], event: dict) -> None:
        async with self._lock:
            sockets = tuple(self._rooms.get(room, ()))
        failed = []
        for socket in sockets:
            try:
                await socket.send_json(event)
            except Exception:
                failed.append(socket)
        for socket in failed:
            await self.leave(room, socket)


hub = RoomHub()


def _role(db: Session, workspace_id: UUID, user_id: UUID, *, write: bool = False) -> str:
    role = db.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if role not in {"owner", "admin", "member", "viewer"}:
        raise HTTPException(status_code=403, detail="Workspace role is invalid")
    if write and role == "viewer":
        raise HTTPException(status_code=403, detail="Viewer access is read-only")
    return role


def _channel(db: Session, workspace_id: UUID, channel_id: UUID) -> ChatChannel:
    channel = db.scalar(
        select(ChatChannel).where(
            ChatChannel.workspace_id == workspace_id,
            ChatChannel.id == channel_id,
        )
    )
    if channel is None:
        raise HTTPException(status_code=404, detail="Chat channel not found")
    return channel


def _message_view(message: ChatMessage, sender_name: str) -> MessageView:
    return MessageView(
        id=message.id,
        workspace_id=message.workspace_id,
        channel_id=message.channel_id,
        sender_user_id=message.sender_user_id,
        sender_display_name=sender_name,
        body=message.body,
        created_at=message.created_at,
    )


def _message_event(view: MessageView) -> dict:
    return {"type": "message", "message": view.model_dump(mode="json")}


@router.get("/channels", response_model=list[ChannelView])
def list_channels(
    workspace_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[ChannelView]:
    _role(db, workspace_id, user.id)
    return list(
        db.scalars(
            select(ChatChannel)
            .where(ChatChannel.workspace_id == workspace_id)
            .order_by(ChatChannel.last_activity_at.desc(), ChatChannel.name)
        ).all()
    )


@router.post("/channels", response_model=ChannelView, status_code=status.HTTP_201_CREATED)
def create_channel(
    workspace_id: UUID,
    payload: ChannelCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ChannelView:
    _role(db, workspace_id, user.id, write=True)
    channel = ChatChannel(workspace_id=workspace_id, name=payload.name, created_by=user.id)
    db.add(channel)
    db.flush()
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="chat.channel_created",
            object_type="chat_channel",
            object_id=channel.id,
            details_json={"name": channel.name},
        )
    )
    db.commit()
    db.refresh(channel)
    return channel


@router.get("/channels/{channel_id}/messages", response_model=list[MessageView])
def list_messages(
    workspace_id: UUID,
    channel_id: UUID,
    limit: int = Query(default=100, ge=1, le=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[MessageView]:
    _role(db, workspace_id, user.id)
    _channel(db, workspace_id, channel_id)
    rows = db.execute(
        select(ChatMessage, User.display_name)
        .join(User, User.id == ChatMessage.sender_user_id)
        .where(ChatMessage.workspace_id == workspace_id, ChatMessage.channel_id == channel_id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(limit)
    ).all()
    return [_message_view(message, name) for message, name in reversed(rows)]


@router.post(
    "/channels/{channel_id}/messages",
    response_model=MessageView,
    status_code=status.HTTP_201_CREATED,
)
async def create_message(
    workspace_id: UUID,
    channel_id: UUID,
    payload: MessageCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MessageView:
    _role(db, workspace_id, user.id, write=True)
    channel = _channel(db, workspace_id, channel_id)
    message = ChatMessage(
        workspace_id=workspace_id,
        channel_id=channel_id,
        sender_user_id=user.id,
        body=payload.body,
    )
    db.add(message)
    channel.last_activity_at = datetime.now(UTC)
    db.flush()
    notify_mentions(
        db,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        text=message.body,
        object_type="chat_message",
        object_id=message.id,
        title=f"{user.display_name} mentioned you in #{channel.name}",
        href="#messages",
    )
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="chat.message_created",
            object_type="chat_message",
            object_id=message.id,
            details_json={"channel_id": str(channel_id)},
        )
    )
    db.commit()
    db.refresh(message)
    view = _message_view(message, user.display_name)
    await hub.publish((workspace_id, channel_id), _message_event(view))
    return view


def _websocket_identity(socket: WebSocket, workspace_id: UUID) -> tuple[UUID, str] | None:
    raw_token = socket.cookies.get(settings.session_cookie_name)
    origin = socket.headers.get("origin")
    allowed = {item.rstrip("/") for item in settings.allowed_origins}
    if origin and origin.rstrip("/") not in allowed or not raw_token:
        return None
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    with SessionLocal() as db:
        row = db.execute(
            select(User.id, WorkspaceMember.role)
            .join(AuthSession, AuthSession.user_id == User.id)
            .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
            .where(
                AuthSession.token_hash == token_hash,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > datetime.now(UTC),
                WorkspaceMember.workspace_id == workspace_id,
            )
        ).first()
        return (row[0], row[1]) if row else None


@router.websocket("/ws")
async def chat_socket(socket: WebSocket, workspace_id: UUID, channel_id: UUID = Query(...)) -> None:
    identity = _websocket_identity(socket, workspace_id)
    if identity is None:
        await socket.close(code=4401)
        return
    user_id, role = identity
    with SessionLocal() as db:
        channel = db.scalar(
            select(ChatChannel).where(
                ChatChannel.workspace_id == workspace_id,
                ChatChannel.id == channel_id,
            )
        )
        person = db.get(User, user_id)
        if channel is None or person is None:
            await socket.close(code=4404)
            return
        sender_name = person.display_name
    await socket.accept()
    room = (workspace_id, channel_id)
    await hub.join(room, socket)
    await hub.publish(
        room,
        {"type": "presence", "user_id": str(user_id), "display_name": sender_name, "online": True},
    )
    try:
        while True:
            event = await socket.receive_json()
            if event.get("type") == "typing" and role != "viewer":
                await hub.publish(
                    room,
                    {
                        "type": "typing",
                        "user_id": str(user_id),
                        "display_name": sender_name,
                        "is_typing": bool(event.get("is_typing")),
                    },
                )
    except WebSocketDisconnect:
        await hub.leave(room, socket)
        await hub.publish(
            room,
            {
                "type": "presence",
                "user_id": str(user_id),
                "display_name": sender_name,
                "online": False,
            },
        )
