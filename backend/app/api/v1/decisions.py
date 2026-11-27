from datetime import date, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.v1.auth import current_user
from app.db.session import get_db
from app.models.entities import (
    AuditEvent,
    DecisionHistory,
    DecisionRecord,
    DecisionReference,
    KnowledgeDocument,
    Project,
    TaskItem,
    User,
    WorkspaceMember,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/projects/{project_id}/decisions", tags=["decisions"]
)
DecisionStatus = Literal["proposed", "approved", "rejected", "superseded"]


class DecisionReferenceInput(BaseModel):
    label: str = Field(min_length=2, max_length=240)
    notes: str = Field(default="", max_length=5000)
    document_id: UUID | None = None
    task_id: UUID | None = None
    source_url: AnyHttpUrl | None = None

    @field_validator("label")
    @classmethod
    def label_not_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Reference label must contain at least two characters")
        return value

    @field_validator("notes")
    @classmethod
    def trim_notes(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def require_one_target(self):
        targets = [
            self.document_id is not None,
            self.task_id is not None,
            self.source_url is not None,
        ]
        if sum(targets) != 1:
            raise ValueError("A reference must point to one document, task, or HTTP(S) URL")
        return self


class DecisionCreate(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    description: str = Field(min_length=1, max_length=20000)
    owner_user_id: UUID | None = None
    decision_date: date = Field(default_factory=date.today)
    reasoning: str = Field(default="", max_length=20000)
    alternatives: list[str] = Field(default_factory=list, max_length=20)
    status: DecisionStatus = "proposed"
    references: list[DecisionReferenceInput] = Field(default_factory=list, max_length=50)

    @field_validator("title", "description")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value

    @field_validator("reasoning")
    @classmethod
    def trim_reasoning(cls, value: str) -> str:
        return value.strip()

    @field_validator("alternatives")
    @classmethod
    def normalize_alternatives(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("Alternatives cannot be blank")
        if any(len(value) > 2000 for value in normalized):
            raise ValueError("Each alternative must be 2,000 characters or fewer")
        return normalized


class DecisionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=240)
    description: str | None = Field(default=None, min_length=1, max_length=20000)
    owner_user_id: UUID | None = None
    decision_date: date | None = None
    reasoning: str | None = Field(default=None, max_length=20000)
    alternatives: list[str] | None = Field(default=None, max_length=20)
    status: DecisionStatus | None = None
    references: list[DecisionReferenceInput] | None = Field(default=None, max_length=50)

    @field_validator("title", "description")
    @classmethod
    def trim_required_non_null_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value

    @field_validator("reasoning")
    @classmethod
    def trim_optional_reasoning(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("alternatives")
    @classmethod
    def normalize_optional_alternatives(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized) or any(len(value) > 2000 for value in normalized):
            raise ValueError("Alternatives must be non-empty and at most 2,000 characters each")
        return normalized

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one decision field to update")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Decision fields cannot be null")
        return self


class DecisionReferenceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID | None
    task_id: UUID | None
    source_url: str | None
    label: str
    notes: str


class DecisionView(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    owner_user_id: UUID
    owner_display_name: str
    created_by: UUID
    title: str
    description: str
    reasoning: str
    alternatives: list[str]
    decision_date: date
    status: DecisionStatus
    version_number: int
    references: list[DecisionReferenceView]
    created_at: datetime
    updated_at: datetime


class DecisionHistoryView(BaseModel):
    id: UUID
    decision_id: UUID
    version_number: int
    snapshot: dict
    edited_by: UUID
    edited_by_name: str
    created_at: datetime


def _workspace_role(db: Session, workspace_id: UUID, user_id: UUID, *, write: bool) -> str:
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


def _project(db: Session, workspace_id: UUID, project_id: UUID) -> Project:
    project = db.scalar(
        select(Project).where(Project.workspace_id == workspace_id, Project.id == project_id)
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _decision(
    db: Session, workspace_id: UUID, project_id: UUID, decision_id: UUID
) -> DecisionRecord:
    decision = db.scalar(
        select(DecisionRecord).where(
            DecisionRecord.workspace_id == workspace_id,
            DecisionRecord.project_id == project_id,
            DecisionRecord.id == decision_id,
        )
    )
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return decision


def _validate_member(db: Session, workspace_id: UUID, user_id: UUID) -> None:
    exists = db.scalar(
        select(WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if exists is None:
        raise HTTPException(status_code=422, detail="Decision owner must be a workspace member")


def _references(db: Session, decision_id: UUID) -> list[DecisionReference]:
    return db.scalars(
        select(DecisionReference)
        .where(DecisionReference.decision_id == decision_id)
        .order_by(DecisionReference.created_at, DecisionReference.id)
    ).all()


def _reference_data(references: list[DecisionReference]) -> list[dict]:
    return [
        {
            "document_id": str(reference.document_id) if reference.document_id else None,
            "task_id": str(reference.task_id) if reference.task_id else None,
            "source_url": reference.source_url,
            "label": reference.label,
            "notes": reference.notes,
        }
        for reference in references
    ]


def _snapshot(db: Session, decision: DecisionRecord) -> dict:
    return {
        "title": decision.title,
        "description": decision.description,
        "owner_user_id": str(decision.owner_user_id),
        "decision_date": decision.decision_date.isoformat(),
        "reasoning": decision.reasoning,
        "alternatives": decision.alternatives,
        "status": decision.status,
        "references": _reference_data(_references(db, decision.id)),
    }


def _decision_view(db: Session, decision: DecisionRecord) -> DecisionView:
    owner_name = db.scalar(select(User.display_name).where(User.id == decision.owner_user_id))
    return DecisionView(
        id=decision.id,
        workspace_id=decision.workspace_id,
        project_id=decision.project_id,
        owner_user_id=decision.owner_user_id,
        owner_display_name=owner_name or "Former member",
        created_by=decision.created_by,
        title=decision.title,
        description=decision.description,
        reasoning=decision.reasoning,
        alternatives=decision.alternatives,
        decision_date=decision.decision_date,
        status=decision.status,
        version_number=decision.version_number,
        references=[
            DecisionReferenceView.model_validate(row) for row in _references(db, decision.id)
        ],
        created_at=decision.created_at,
        updated_at=decision.updated_at,
    )


def _validate_references(
    db: Session,
    workspace_id: UUID,
    references: list[DecisionReferenceInput],
) -> None:
    for reference in references:
        if reference.document_id:
            exists = db.scalar(
                select(KnowledgeDocument.id).where(
                    KnowledgeDocument.workspace_id == workspace_id,
                    KnowledgeDocument.id == reference.document_id,
                )
            )
            if exists is None:
                raise HTTPException(
                    status_code=422, detail="Evidence document is outside this workspace"
                )
        if reference.task_id:
            exists = db.scalar(
                select(TaskItem.id).where(
                    TaskItem.workspace_id == workspace_id,
                    TaskItem.id == reference.task_id,
                )
            )
            if exists is None:
                raise HTTPException(
                    status_code=422, detail="Evidence task is outside this workspace"
                )


def _insert_references(
    db: Session,
    decision: DecisionRecord,
    user_id: UUID,
    references: list[DecisionReferenceInput],
) -> None:
    _validate_references(db, decision.workspace_id, references)
    db.execute(delete(DecisionReference).where(DecisionReference.decision_id == decision.id))
    for reference in references:
        db.add(
            DecisionReference(
                workspace_id=decision.workspace_id,
                decision_id=decision.id,
                document_id=reference.document_id,
                task_id=reference.task_id,
                source_url=str(reference.source_url) if reference.source_url else None,
                label=reference.label,
                notes=reference.notes,
                created_by=user_id,
            )
        )
    db.flush()


def _record_history(db: Session, decision: DecisionRecord, user_id: UUID) -> None:
    db.add(
        DecisionHistory(
            decision_id=decision.id,
            version_number=decision.version_number,
            snapshot_json=_snapshot(db, decision),
            edited_by=user_id,
        )
    )


@router.get("", response_model=list[DecisionView])
def list_decisions(
    workspace_id: UUID,
    project_id: UUID,
    q: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[DecisionView]:
    _workspace_role(db, workspace_id, user.id, write=False)
    _project(db, workspace_id, project_id)
    query = select(DecisionRecord).where(
        DecisionRecord.workspace_id == workspace_id, DecisionRecord.project_id == project_id
    )
    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(
            DecisionRecord.title.ilike(pattern)
            | DecisionRecord.description.ilike(pattern)
            | DecisionRecord.reasoning.ilike(pattern)
        )
    decisions = db.scalars(query.order_by(DecisionRecord.decision_date.desc())).all()
    return [_decision_view(db, decision) for decision in decisions]


@router.post("", response_model=DecisionView, status_code=status.HTTP_201_CREATED)
def create_decision(
    workspace_id: UUID,
    project_id: UUID,
    payload: DecisionCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DecisionView:
    _workspace_role(db, workspace_id, user.id, write=True)
    _project(db, workspace_id, project_id)
    owner_id = payload.owner_user_id or user.id
    _validate_member(db, workspace_id, owner_id)
    _validate_references(db, workspace_id, payload.references)
    decision = DecisionRecord(
        workspace_id=workspace_id,
        project_id=project_id,
        owner_user_id=owner_id,
        created_by=user.id,
        title=payload.title.strip(),
        description=payload.description.strip(),
        reasoning=payload.reasoning.strip(),
        alternatives=payload.alternatives,
        decision_date=payload.decision_date,
        status=payload.status,
        version_number=1,
    )
    db.add(decision)
    db.flush()
    _insert_references(db, decision, user.id, payload.references)
    _record_history(db, decision, user.id)
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=user.id,
            event_type="decision.created",
            object_type="decision",
            object_id=decision.id,
            details_json={"project_id": str(project_id), "title": decision.title},
        )
    )
    db.commit()
    db.refresh(decision)
    return _decision_view(db, decision)


@router.get("/{decision_id}", response_model=DecisionView)
def get_decision(
    workspace_id: UUID,
    project_id: UUID,
    decision_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DecisionView:
    _workspace_role(db, workspace_id, user.id, write=False)
    _project(db, workspace_id, project_id)
    return _decision_view(db, _decision(db, workspace_id, project_id, decision_id))


@router.patch("/{decision_id}", response_model=DecisionView)
def update_decision(
    workspace_id: UUID,
    project_id: UUID,
    decision_id: UUID,
    payload: DecisionUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DecisionView:
    _workspace_role(db, workspace_id, user.id, write=True)
    _project(db, workspace_id, project_id)
    decision = _decision(db, workspace_id, project_id, decision_id)
    changes = payload.model_dump(exclude_unset=True, exclude={"references"})
    if "owner_user_id" in changes:
        _validate_member(db, workspace_id, changes["owner_user_id"])
    has_reference_changes = "references" in payload.model_fields_set
    reference_inputs = payload.references or []
    if has_reference_changes:
        _validate_references(db, workspace_id, reference_inputs)
    value_changes = {
        field: value for field, value in changes.items() if getattr(decision, field) != value
    }
    current_refs = _reference_data(_references(db, decision.id))
    new_refs = [
        {
            "document_id": str(reference.document_id) if reference.document_id else None,
            "task_id": str(reference.task_id) if reference.task_id else None,
            "source_url": str(reference.source_url) if reference.source_url else None,
            "label": reference.label,
            "notes": reference.notes,
        }
        for reference in reference_inputs
    ]
    references_changed = has_reference_changes and current_refs != new_refs
    if not value_changes and not references_changed:
        raise HTTPException(status_code=422, detail="No decision values changed")
    for field, value in value_changes.items():
        setattr(decision, field, value)
    if references_changed:
        _insert_references(db, decision, user.id, reference_inputs)
    decision.version_number += 1
    db.flush()
    _record_history(db, decision, user.id)
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=user.id,
            event_type="decision.updated",
            object_type="decision",
            object_id=decision.id,
            details_json={
                "project_id": str(project_id),
                "title": decision.title,
                "changed_fields": sorted(
                    [*value_changes, *(["references"] if references_changed else [])]
                ),
            },
        )
    )
    db.commit()
    db.refresh(decision)
    return _decision_view(db, decision)


@router.get("/{decision_id}/history", response_model=list[DecisionHistoryView])
def list_decision_history(
    workspace_id: UUID,
    project_id: UUID,
    decision_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[DecisionHistoryView]:
    _workspace_role(db, workspace_id, user.id, write=False)
    _project(db, workspace_id, project_id)
    _decision(db, workspace_id, project_id, decision_id)
    rows = db.execute(
        select(DecisionHistory, User.display_name)
        .join(User, User.id == DecisionHistory.edited_by)
        .where(DecisionHistory.decision_id == decision_id)
        .order_by(DecisionHistory.version_number.desc())
    ).all()
    return [
        DecisionHistoryView(
            id=history.id,
            decision_id=history.decision_id,
            version_number=history.version_number,
            snapshot=history.snapshot_json,
            edited_by=history.edited_by,
            edited_by_name=display_name,
            created_at=history.created_at,
        )
        for history, display_name in rows
    ]
