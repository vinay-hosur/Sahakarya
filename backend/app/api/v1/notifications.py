from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.v1.auth import current_user
from app.db.session import get_db
from app.models.entities import User, WorkspaceMember, WorkspaceNotification

router = APIRouter(prefix="/workspaces/{workspace_id}/notifications", tags=["notifications"])


class NotificationView(BaseModel):
    id: UUID
    workspace_id: UUID
    recipient_user_id: UUID
    actor_user_id: UUID | None
    actor_display_name: str | None
    event_type: str
    title: str
    body: str
    object_type: str | None
    object_id: UUID | None
    href: str
    read_at: datetime | None
    created_at: datetime


def _require_member(db: Session, workspace_id: UUID, user_id: UUID) -> None:
    member_id = db.scalar(
        select(WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if member_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")


def _notification_view(
    notification: WorkspaceNotification, actor_name: str | None
) -> NotificationView:
    return NotificationView(
        id=notification.id,
        workspace_id=notification.workspace_id,
        recipient_user_id=notification.recipient_user_id,
        actor_user_id=notification.actor_user_id,
        actor_display_name=actor_name,
        event_type=notification.event_type,
        title=notification.title,
        body=notification.body,
        object_type=notification.object_type,
        object_id=notification.object_id,
        href=notification.href,
        read_at=notification.read_at,
        created_at=notification.created_at,
    )


@router.get("", response_model=list[NotificationView])
def list_notifications(
    workspace_id: UUID,
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[NotificationView]:
    _require_member(db, workspace_id, user.id)
    query = (
        select(WorkspaceNotification, User.display_name)
        .outerjoin(User, User.id == WorkspaceNotification.actor_user_id)
        .where(
            WorkspaceNotification.workspace_id == workspace_id,
            WorkspaceNotification.recipient_user_id == user.id,
        )
    )
    if unread_only:
        query = query.where(WorkspaceNotification.read_at.is_(None))
    rows = db.execute(
        query.order_by(
            WorkspaceNotification.created_at.desc(), WorkspaceNotification.id.desc()
        ).limit(limit)
    ).all()
    return [_notification_view(notification, actor_name) for notification, actor_name in rows]


@router.patch("/{notification_id}/read", response_model=NotificationView)
def mark_notification_read(
    workspace_id: UUID,
    notification_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> NotificationView:
    _require_member(db, workspace_id, user.id)
    notification = db.scalar(
        select(WorkspaceNotification).where(
            WorkspaceNotification.id == notification_id,
            WorkspaceNotification.workspace_id == workspace_id,
            WorkspaceNotification.recipient_user_id == user.id,
        )
    )
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        db.commit()
        db.refresh(notification)
    actor_name = (
        db.scalar(select(User.display_name).where(User.id == notification.actor_user_id))
        if notification.actor_user_id
        else None
    )
    return _notification_view(notification, actor_name)


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_notifications_read(
    workspace_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    _require_member(db, workspace_id, user.id)
    db.execute(
        update(WorkspaceNotification)
        .where(
            WorkspaceNotification.workspace_id == workspace_id,
            WorkspaceNotification.recipient_user_id == user.id,
            WorkspaceNotification.read_at.is_(None),
        )
        .values(read_at=datetime.now(UTC))
    )
    db.commit()
