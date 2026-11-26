from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.api.v1.auth import current_user
from app.db.session import get_db
from app.models.entities import AuditEvent, Project, User, WorkspaceMember

router = APIRouter(prefix="/workspaces/{workspace_id}/activity", tags=["activity"])


class ActivityView(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    actor_user_id: UUID | None
    actor_display_name: str
    event_type: str
    object_type: str
    object_id: UUID | None
    summary: str
    created_at: datetime


def _summary(event_type: str, object_type: str, details: dict) -> str:
    if event_type.endswith(".comment_created"):
        noun = "document" if object_type == "document_comment" else "task"
        return f"Added a comment to a {noun}"
    if event_type.endswith(".created"):
        title = details.get("title") or details.get("name")
        if isinstance(title, str) and title:
            return f"Created {object_type.replace('_', ' ')}: {title}"
        return f"Created {object_type.replace('_', ' ')}"
    if event_type.endswith(".updated"):
        fields = details.get("changed_fields")
        if isinstance(fields, list) and fields and all(isinstance(field, str) for field in fields):
            return f"Updated {object_type.replace('_', ' ')} fields: {', '.join(fields)}"
        title = details.get("title")
        return (
            f"Updated {object_type.replace('_', ' ')}: {title}"
            if title
            else f"Updated {object_type.replace('_', ' ')}"
        )
    if event_type.endswith(".approved"):
        return f"Approved {object_type.replace('_', ' ')}"
    if event_type.endswith(".succeeded"):
        return f"Completed {object_type.replace('_', ' ')}"
    words = event_type.replace(".", " ").replace("_", " ")
    return words[:1].upper() + words[1:]


@router.get("", response_model=list[ActivityView])
def list_activity(
    workspace_id: UUID,
    project_id: UUID | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[ActivityView]:
    role = db.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if role not in {"owner", "member", "viewer"}:
        raise HTTPException(status_code=403, detail="Workspace role is invalid")

    query = (
        select(AuditEvent, User.display_name)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .where(AuditEvent.workspace_id == workspace_id)
    )
    if project_id is not None:
        project_exists = db.scalar(
            select(Project.id).where(
                Project.id == project_id,
                Project.workspace_id == workspace_id,
            )
        )
        if project_exists is None:
            raise HTTPException(status_code=404, detail="Project not found")
        query = query.where(
            or_(
                AuditEvent.project_id == project_id,
                and_(AuditEvent.object_type == "project", AuditEvent.object_id == project_id),
                AuditEvent.details_json["project_id"].as_string() == str(project_id),
            )
        )
    rows = db.execute(
        query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    result: list[ActivityView] = []
    for event, actor_name in rows:
        event_project_id = event.project_id
        if event_project_id is None and event.object_type == "project":
            event_project_id = event.object_id
        if event_project_id is None:
            details_project_id = event.details_json.get("project_id")
            try:
                event_project_id = UUID(details_project_id) if details_project_id else None
            except (ValueError, TypeError):
                event_project_id = None
        result.append(
            ActivityView(
                id=event.id,
                workspace_id=event.workspace_id,
                project_id=event_project_id,
                actor_user_id=event.actor_user_id,
                actor_display_name=actor_name or "Former member",
                event_type=event.event_type,
                object_type=event.object_type,
                object_id=event.object_id,
                summary=_summary(event.event_type, event.object_type, event.details_json),
                created_at=event.created_at,
            )
        )
    return result
