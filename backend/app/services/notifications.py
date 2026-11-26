import re
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import User, WorkspaceMember, WorkspaceNotification

MENTION_PATTERN = re.compile(r"(?<![\w.+-])@([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", re.IGNORECASE)


def notify_member(
    db: Session,
    *,
    workspace_id: UUID,
    recipient_user_id: UUID,
    actor_user_id: UUID,
    event_type: str,
    title: str,
    body: str = "",
    object_type: str | None = None,
    object_id: UUID | None = None,
    href: str = "#overview",
) -> None:
    if recipient_user_id == actor_user_id:
        return
    is_member = db.scalar(
        select(WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == recipient_user_id,
        )
    )
    if is_member is None:
        return
    db.add(
        WorkspaceNotification(
            workspace_id=workspace_id,
            recipient_user_id=recipient_user_id,
            actor_user_id=actor_user_id,
            event_type=event_type[:80],
            title=title[:180],
            body=body[:500],
            object_type=object_type[:80] if object_type else None,
            object_id=object_id,
            href=href if href.startswith("#") else "#overview",
        )
    )


def notify_mentions(
    db: Session,
    *,
    workspace_id: UUID,
    actor_user_id: UUID,
    text: str,
    object_type: str,
    object_id: UUID,
    title: str,
    href: str,
) -> None:
    emails = {email.casefold() for email in MENTION_PATTERN.findall(text)}
    if not emails:
        return
    mentioned = db.scalars(
        select(WorkspaceMember.user_id)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            func.lower(User.email).in_(emails),
        )
    ).all()
    for recipient_user_id in mentioned:
        notify_member(
            db,
            workspace_id=workspace_id,
            recipient_user_id=recipient_user_id,
            actor_user_id=actor_user_id,
            event_type="mention",
            title=title,
            body=text,
            object_type=object_type,
            object_id=object_id,
            href=href,
        )
