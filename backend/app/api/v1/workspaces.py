import secrets
import unicodedata
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.auth import current_user
from app.db.session import get_db
from app.models.entities import AuditEvent, User, Workspace, WorkspaceMember

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Workspace name must contain at least two characters")
        return value


class WorkspaceUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=160)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Workspace name must contain at least two characters")
        return value


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    slug: str
    role: str


def _slug_prefix(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    slug = "".join(character if character.isalnum() else "-" for character in plain)
    return "-".join(part for part in slug.split("-") if part)[:62].strip("-") or "workspace"


def _workspace_response(workspace: Workspace, role: str) -> WorkspaceOut:
    return WorkspaceOut(id=workspace.id, name=workspace.name, slug=workspace.slug, role=role)


@router.get("", response_model=list[WorkspaceOut])
def list_workspaces(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.created_at)
    ).all()
    return [_workspace_response(workspace, role) for workspace, role in rows]


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(
    payload: WorkspaceCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    workspace = Workspace(
        name=name,
        slug=f"{_slug_prefix(name)}-{secrets.token_hex(3)}",
        owner_user_id=user.id,
    )
    db.add(workspace)
    try:
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.add(
            AuditEvent(
                workspace_id=workspace.id,
                actor_user_id=user.id,
                event_type="workspace.created",
                object_type="workspace",
                object_id=workspace.id,
                details_json={"name": name},
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Could not create workspace") from exc
    return _workspace_response(workspace, "owner")


@router.get("/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(
    workspace_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    row = db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(Workspace.id == workspace_id, WorkspaceMember.user_id == user.id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return _workspace_response(row[0], row[1])


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
def update_workspace(
    workspace_id: UUID,
    payload: WorkspaceUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    row = db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(Workspace.id == workspace_id, WorkspaceMember.user_id == user.id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace, role = row
    if role != "owner":
        raise HTTPException(status_code=403, detail="Only workspace owners can rename it")

    workspace.name = payload.name.strip()
    db.add(
        AuditEvent(
            workspace_id=workspace.id,
            actor_user_id=user.id,
            event_type="workspace.renamed",
            object_type="workspace",
            object_id=workspace.id,
            details_json={"name": workspace.name},
        )
    )
    db.commit()
    return _workspace_response(workspace, role)
