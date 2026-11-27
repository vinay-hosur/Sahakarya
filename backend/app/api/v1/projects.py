from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.auth import current_user
from app.db.session import get_db
from app.models.entities import AuditEvent, Project, User, WorkspaceMember

router = APIRouter(prefix="/workspaces/{workspace_id}/projects", tags=["projects"])
ProjectStatus = Literal["active", "on_hold", "completed"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=5000)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Project name must contain at least two characters")
        return value

    @field_validator("description")
    @classmethod
    def description_is_trimmed(cls, value: str) -> str:
        return value.strip()


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=5000)
    status: ProjectStatus | None = None

    @field_validator("name")
    @classmethod
    def optional_name_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Project name must contain at least two characters")
        return value

    @field_validator("description")
    @classmethod
    def optional_description_is_trimmed(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_a_change(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one project field to update")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Project fields cannot be null")
        return self


class ProjectView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    name: str
    description: str
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime


def _workspace_role(db: Session, workspace_id: UUID, user_id: UUID, *, write: bool = False) -> str:
    role = db.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    if role not in {"owner", "admin", "member", "viewer"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Workspace role is invalid"
        )
    if write and role == "viewer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Project access is read-only"
        )
    return role


def _project_or_404(db: Session, workspace_id: UUID, project_id: UUID) -> Project:
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.workspace_id == workspace_id)
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


@router.get("", response_model=list[ProjectView])
def list_projects(
    workspace_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[ProjectView]:
    _workspace_role(db, workspace_id, user.id)
    projects = db.scalars(
        select(Project).where(Project.workspace_id == workspace_id).order_by(Project.created_at)
    ).all()
    return [ProjectView.model_validate(project) for project in projects]


@router.post("", response_model=ProjectView, status_code=status.HTTP_201_CREATED)
def create_project(
    workspace_id: UUID,
    payload: ProjectCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProjectView:
    _workspace_role(db, workspace_id, user.id, write=True)
    project = Project(
        workspace_id=workspace_id,
        name=payload.name,
        description=payload.description,
        status="active",
    )
    db.add(project)
    db.flush()
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            project_id=project.id,
            actor_user_id=user.id,
            event_type="project.created",
            object_type="project",
            object_id=project.id,
            details_json={"name": project.name},
        )
    )
    db.commit()
    db.refresh(project)
    return ProjectView.model_validate(project)


@router.get("/{project_id}", response_model=ProjectView)
def get_project(
    workspace_id: UUID,
    project_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProjectView:
    _workspace_role(db, workspace_id, user.id)
    return ProjectView.model_validate(_project_or_404(db, workspace_id, project_id))


@router.patch("/{project_id}", response_model=ProjectView)
def update_project(
    workspace_id: UUID,
    project_id: UUID,
    payload: ProjectUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProjectView:
    _workspace_role(db, workspace_id, user.id, write=True)
    project = _project_or_404(db, workspace_id, project_id)
    changes = payload.model_dump(exclude_unset=True)
    previous = {field: getattr(project, field) for field in changes}
    for field, value in changes.items():
        setattr(project, field, value)
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            project_id=project.id,
            actor_user_id=user.id,
            event_type="project.updated",
            object_type="project",
            object_id=project.id,
            details_json={"changed_fields": sorted(changes), "previous": previous},
        )
    )
    db.commit()
    db.refresh(project)
    return ProjectView.model_validate(project)
