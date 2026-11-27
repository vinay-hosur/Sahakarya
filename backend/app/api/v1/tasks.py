from datetime import date, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.v1.auth import current_user
from app.db.session import get_db
from app.models.entities import (
    AuditEvent,
    Project,
    TaskComment,
    TaskDependency,
    TaskHistory,
    TaskItem,
    User,
    WorkspaceMember,
)
from app.services.notifications import notify_member, notify_mentions

router = APIRouter(prefix="/workspaces/{workspace_id}/projects/{project_id}/tasks", tags=["tasks"])
TaskStatus = Literal["todo", "in_progress", "blocked", "done"]
TaskPriority = Literal["low", "medium", "high", "urgent"]


class TaskCreate(BaseModel):
    title: str = Field(min_length=2, max_length=256)
    description: str = Field(default="", max_length=10000)
    priority: TaskPriority = "medium"
    due_date: date | None = None
    assignee_id: UUID | None = None
    labels: list[str] = Field(default_factory=list, max_length=10)
    depends_on_task_ids: list[UUID] = Field(default_factory=list, max_length=25)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Task title must contain at least two characters")
        return value

    @field_validator("description")
    @classmethod
    def description_is_trimmed(cls, value: str) -> str:
        return value.strip()

    @field_validator("labels")
    @classmethod
    def normalize_labels(cls, values: list[str]) -> list[str]:
        return _normalize_labels(values)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=256)
    description: str | None = Field(default=None, max_length=10000)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_date: date | None = None
    assignee_id: UUID | None = None
    labels: list[str] | None = Field(default=None, max_length=10)
    depends_on_task_ids: list[UUID] | None = Field(default=None, max_length=25)

    @field_validator("title")
    @classmethod
    def optional_title_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Task title must contain at least two characters")
        return value

    @field_validator("description")
    @classmethod
    def optional_description_is_trimmed(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("labels")
    @classmethod
    def optional_labels_are_normalized(cls, values: list[str] | None) -> list[str] | None:
        return _normalize_labels(values) if values is not None else None

    @model_validator(mode="after")
    def require_a_change(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one task field to update")
        invalid_nulls = self.model_fields_set - {"assignee_id", "due_date"}
        if any(getattr(self, field) is None for field in invalid_nulls):
            raise ValueError("Task fields cannot be null")
        return self


class TaskView(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    created_by: UUID
    assignee_id: UUID | None
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    due_date: date | None
    labels: list[str]
    depends_on_task_ids: list[UUID]
    created_at: datetime
    updated_at: datetime


class TaskHistoryView(BaseModel):
    id: UUID
    task_id: UUID
    actor_user_id: UUID
    actor_display_name: str
    event_type: str
    changes: dict
    created_at: datetime


class TaskCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)

    @field_validator("body")
    @classmethod
    def body_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Comment cannot be blank")
        return value


class TaskCommentView(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    task_id: UUID
    author_user_id: UUID
    author_display_name: str
    body: str
    created_at: datetime


def _workspace_access(db: Session, workspace_id: UUID, user_id: UUID, *, write: bool) -> None:
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
            status_code=status.HTTP_403_FORBIDDEN, detail="Viewer access is read-only"
        )


def _project(db: Session, workspace_id: UUID, project_id: UUID) -> Project:
    project = db.scalar(
        select(Project).where(Project.workspace_id == workspace_id, Project.id == project_id)
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _task(db: Session, workspace_id: UUID, project_id: UUID, task_id: UUID) -> TaskItem:
    task = db.scalar(
        select(TaskItem).where(
            TaskItem.workspace_id == workspace_id,
            TaskItem.project_id == project_id,
            TaskItem.id == task_id,
        )
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


def _validate_assignee(db: Session, workspace_id: UUID, assignee_id: UUID | None) -> None:
    if assignee_id is None:
        return
    member = db.scalar(
        select(WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == assignee_id,
        )
    )
    if member is None:
        raise HTTPException(status_code=422, detail="Assignee must be a workspace member")


def _normalize_labels(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        label = raw.strip()
        if not label:
            continue
        if len(label) > 32 or any(ord(char) < 32 or ord(char) == 127 for char in label):
            raise ValueError("Labels must be at most 32 printable characters")
        key = label.casefold()
        if key not in seen:
            normalized.append(label)
            seen.add(key)
    if len(normalized) > 10:
        raise ValueError("A task can have at most 10 labels")
    return normalized


def _dependency_ids(db: Session, workspace_id: UUID, project_id: UUID, task_id: UUID) -> list[UUID]:
    return list(
        db.scalars(
            select(TaskDependency.depends_on_task_id)
            .where(
                TaskDependency.workspace_id == workspace_id,
                TaskDependency.project_id == project_id,
                TaskDependency.task_id == task_id,
            )
            .order_by(TaskDependency.created_at, TaskDependency.depends_on_task_id)
        ).all()
    )


def _task_view(db: Session, task: TaskItem) -> TaskView:
    return TaskView(
        id=task.id,
        workspace_id=task.workspace_id,
        project_id=task.project_id,
        created_by=task.created_by,
        assignee_id=task.assignee_id,
        title=task.title,
        description=task.description,
        status=task.status,
        priority=task.priority,
        due_date=task.due_date,
        labels=task.labels_json,
        depends_on_task_ids=_dependency_ids(db, task.workspace_id, task.project_id, task.id),
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _validate_dependencies(
    db: Session,
    workspace_id: UUID,
    project_id: UUID,
    task_id: UUID,
    dependency_ids: list[UUID],
) -> None:
    if len(set(dependency_ids)) != len(dependency_ids):
        raise HTTPException(status_code=422, detail="Task dependencies must be unique")
    if task_id in dependency_ids:
        raise HTTPException(status_code=422, detail="A task cannot depend on itself")
    if not dependency_ids:
        return
    found = set(
        db.scalars(
            select(TaskItem.id).where(
                TaskItem.workspace_id == workspace_id,
                TaskItem.project_id == project_id,
                TaskItem.id.in_(dependency_ids),
            )
        ).all()
    )
    if found != set(dependency_ids):
        raise HTTPException(
            status_code=422, detail="Dependencies must be tasks in the same project"
        )

    edges = db.execute(
        select(TaskDependency.task_id, TaskDependency.depends_on_task_id).where(
            TaskDependency.workspace_id == workspace_id,
            TaskDependency.project_id == project_id,
            TaskDependency.task_id != task_id,
        )
    ).all()
    graph: dict[UUID, list[UUID]] = {}
    for source_id, target_id in edges:
        graph.setdefault(source_id, []).append(target_id)
    for dependency_id in dependency_ids:
        pending = [dependency_id]
        visited: set[UUID] = set()
        while pending:
            current = pending.pop()
            if current == task_id:
                raise HTTPException(
                    status_code=422, detail="Task dependencies cannot contain a cycle"
                )
            if current in visited:
                continue
            visited.add(current)
            pending.extend(graph.get(current, []))


def _replace_dependencies(
    db: Session,
    task: TaskItem,
    dependency_ids: list[UUID],
    actor_user_id: UUID,
) -> None:
    db.execute(
        delete(TaskDependency).where(
            TaskDependency.workspace_id == task.workspace_id,
            TaskDependency.project_id == task.project_id,
            TaskDependency.task_id == task.id,
        )
    )
    db.add_all(
        TaskDependency(
            workspace_id=task.workspace_id,
            project_id=task.project_id,
            task_id=task.id,
            depends_on_task_id=dependency_id,
            created_by=actor_user_id,
        )
        for dependency_id in dependency_ids
    )


def _record_task_history(
    db: Session,
    task: TaskItem,
    actor_user_id: UUID,
    event_type: str,
    changes: dict,
) -> None:
    db.add(
        TaskHistory(
            workspace_id=task.workspace_id,
            project_id=task.project_id,
            task_id=task.id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            changes_json=jsonable_encoder(changes),
        )
    )


@router.get("", response_model=list[TaskView])
def list_tasks(
    workspace_id: UUID,
    project_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[TaskView]:
    _workspace_access(db, workspace_id, user.id, write=False)
    _project(db, workspace_id, project_id)
    tasks = db.scalars(
        select(TaskItem)
        .where(TaskItem.workspace_id == workspace_id, TaskItem.project_id == project_id)
        .order_by(TaskItem.created_at.desc())
    ).all()
    return [_task_view(db, task) for task in tasks]


@router.post("", response_model=TaskView, status_code=status.HTTP_201_CREATED)
def create_task(
    workspace_id: UUID,
    project_id: UUID,
    payload: TaskCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TaskView:
    _workspace_access(db, workspace_id, user.id, write=True)
    _project(db, workspace_id, project_id)
    _validate_assignee(db, workspace_id, payload.assignee_id)
    task = TaskItem(
        workspace_id=workspace_id,
        project_id=project_id,
        created_by=user.id,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        due_date=payload.due_date,
        assignee_id=payload.assignee_id,
        labels_json=payload.labels,
        status="todo",
    )
    db.add(task)
    db.flush()
    _validate_dependencies(db, workspace_id, project_id, task.id, payload.depends_on_task_ids)
    _replace_dependencies(db, task, payload.depends_on_task_ids, user.id)
    db.flush()
    if task.assignee_id is not None:
        notify_member(
            db,
            workspace_id=workspace_id,
            recipient_user_id=task.assignee_id,
            actor_user_id=user.id,
            event_type="task.assigned",
            title=f"You were assigned {task.title}",
            body=task.title,
            object_type="task",
            object_id=task.id,
            href="#projects",
        )
    _record_task_history(
        db,
        task,
        user.id,
        "created",
        {"after": _task_view(db, task).model_dump(mode="json")},
    )
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=user.id,
            event_type="task.created",
            object_type="task",
            object_id=task.id,
            details_json={"project_id": str(project_id), "title": task.title},
        )
    )
    db.commit()
    db.refresh(task)
    return _task_view(db, task)


@router.get("/{task_id}", response_model=TaskView)
def get_task(
    workspace_id: UUID,
    project_id: UUID,
    task_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TaskView:
    _workspace_access(db, workspace_id, user.id, write=False)
    _project(db, workspace_id, project_id)
    return _task_view(db, _task(db, workspace_id, project_id, task_id))


@router.patch("/{task_id}", response_model=TaskView)
def update_task(
    workspace_id: UUID,
    project_id: UUID,
    task_id: UUID,
    payload: TaskUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TaskView:
    _workspace_access(db, workspace_id, user.id, write=True)
    _project(db, workspace_id, project_id)
    task = _task(db, workspace_id, project_id, task_id)
    previous_dependencies = _dependency_ids(db, workspace_id, project_id, task.id)
    previous_assignee_id = task.assignee_id
    dependency_ids: list[UUID] | None = None
    changes = payload.model_dump(exclude_unset=True, exclude={"depends_on_task_ids"})
    if "depends_on_task_ids" in payload.model_fields_set:
        dependency_ids = payload.depends_on_task_ids
        assert dependency_ids is not None
        _validate_dependencies(db, workspace_id, project_id, task.id, dependency_ids)
        _replace_dependencies(db, task, dependency_ids, user.id)
    if "assignee_id" in changes:
        _validate_assignee(db, workspace_id, changes["assignee_id"])
    history_changes = {}
    previous = {}
    for field, value in changes.items():
        attribute = "labels_json" if field == "labels" else field
        old_value = getattr(task, attribute)
        previous[field] = jsonable_encoder(old_value)
        setattr(task, attribute, value)
        history_changes[field] = {
            "before": jsonable_encoder(old_value),
            "after": jsonable_encoder(value),
        }
    if dependency_ids is not None:
        previous["depends_on_task_ids"] = [str(value) for value in previous_dependencies]
        history_changes["depends_on_task_ids"] = {
            "before": [str(value) for value in previous_dependencies],
            "after": [str(value) for value in dependency_ids],
        }
    if "assignee_id" in changes and changes["assignee_id"] != previous_assignee_id:
        if previous_assignee_id is not None:
            notify_member(
                db,
                workspace_id=workspace_id,
                recipient_user_id=previous_assignee_id,
                actor_user_id=user.id,
                event_type="task.unassigned",
                title=f"You are no longer assigned to {task.title}",
                body=task.title,
                object_type="task",
                object_id=task.id,
                href="#projects",
            )
        if task.assignee_id is not None:
            notify_member(
                db,
                workspace_id=workspace_id,
                recipient_user_id=task.assignee_id,
                actor_user_id=user.id,
                event_type="task.assigned",
                title=f"You were assigned {task.title}",
                body=task.title,
                object_type="task",
                object_id=task.id,
                href="#projects",
            )
    _record_task_history(db, task, user.id, "updated", history_changes)
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=user.id,
            event_type="task.updated",
            object_type="task",
            object_id=task.id,
            details_json={
                "project_id": str(project_id),
                "changed_fields": sorted(history_changes),
                "previous": previous,
            },
        )
    )
    db.commit()
    db.refresh(task)
    return _task_view(db, task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    workspace_id: UUID,
    project_id: UUID,
    task_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    _workspace_access(db, workspace_id, user.id, write=True)
    _project(db, workspace_id, project_id)
    task = _task(db, workspace_id, project_id, task_id)
    dependents = db.scalar(
        select(TaskDependency.id).where(
            TaskDependency.workspace_id == workspace_id,
            TaskDependency.project_id == project_id,
            TaskDependency.depends_on_task_id == task_id,
        )
    )
    if dependents:
        raise HTTPException(
            status_code=409,
            detail="Remove this task from dependent tasks before deleting it.",
        )
    _record_task_history(
        db,
        task,
        user.id,
        "deleted",
        {"before": _task_view(db, task).model_dump(mode="json")},
    )
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=user.id,
            event_type="task.deleted",
            object_type="task",
            object_id=task.id,
            details_json={"project_id": str(project_id), "title": task.title},
        )
    )
    db.delete(task)
    db.commit()


@router.get("/{task_id}/history", response_model=list[TaskHistoryView])
def list_task_history(
    workspace_id: UUID,
    project_id: UUID,
    task_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[TaskHistoryView]:
    _workspace_access(db, workspace_id, user.id, write=False)
    _project(db, workspace_id, project_id)
    _task(db, workspace_id, project_id, task_id)
    rows = db.execute(
        select(TaskHistory, User.display_name)
        .join(User, User.id == TaskHistory.actor_user_id)
        .where(
            TaskHistory.workspace_id == workspace_id,
            TaskHistory.project_id == project_id,
            TaskHistory.task_id == task_id,
        )
        .order_by(TaskHistory.created_at.desc(), TaskHistory.id.desc())
    ).all()
    return [
        TaskHistoryView(
            id=entry.id,
            task_id=entry.task_id,
            actor_user_id=entry.actor_user_id,
            actor_display_name=display_name,
            event_type=entry.event_type,
            changes=entry.changes_json,
            created_at=entry.created_at,
        )
        for entry, display_name in rows
    ]


@router.get("/{task_id}/comments", response_model=list[TaskCommentView])
def list_task_comments(
    workspace_id: UUID,
    project_id: UUID,
    task_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[TaskCommentView]:
    _workspace_access(db, workspace_id, user.id, write=False)
    _project(db, workspace_id, project_id)
    _task(db, workspace_id, project_id, task_id)
    rows = db.execute(
        select(TaskComment, User.display_name)
        .join(User, User.id == TaskComment.author_user_id)
        .where(
            TaskComment.workspace_id == workspace_id,
            TaskComment.project_id == project_id,
            TaskComment.task_id == task_id,
        )
        .order_by(TaskComment.created_at, TaskComment.id)
    ).all()
    return [
        TaskCommentView(
            id=comment.id,
            workspace_id=comment.workspace_id,
            project_id=comment.project_id,
            task_id=comment.task_id,
            author_user_id=comment.author_user_id,
            author_display_name=display_name,
            body=comment.body,
            created_at=comment.created_at,
        )
        for comment, display_name in rows
    ]


@router.post(
    "/{task_id}/comments", response_model=TaskCommentView, status_code=status.HTTP_201_CREATED
)
def create_task_comment(
    workspace_id: UUID,
    project_id: UUID,
    task_id: UUID,
    payload: TaskCommentCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TaskCommentView:
    _workspace_access(db, workspace_id, user.id, write=True)
    _project(db, workspace_id, project_id)
    task = _task(db, workspace_id, project_id, task_id)
    comment = TaskComment(
        workspace_id=workspace_id,
        project_id=project_id,
        task_id=task_id,
        author_user_id=user.id,
        body=payload.body,
    )
    db.add(comment)
    db.flush()
    recipients = {task.created_by, task.assignee_id} - {None, user.id}
    for recipient_user_id in recipients:
        notify_member(
            db,
            workspace_id=workspace_id,
            recipient_user_id=recipient_user_id,
            actor_user_id=user.id,
            event_type="task.comment",
            title=f"New comment on {task.title}",
            body=comment.body,
            object_type="task_comment",
            object_id=comment.id,
            href="#projects",
        )
    notify_mentions(
        db,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        text=comment.body,
        object_type="task_comment",
        object_id=comment.id,
        title=f"{user.display_name} mentioned you in {task.title}",
        href="#projects",
    )
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=user.id,
            event_type="task.comment_created",
            object_type="task_comment",
            object_id=comment.id,
            details_json={"project_id": str(project_id), "task_id": str(task_id)},
        )
    )
    db.commit()
    db.refresh(comment)
    return TaskCommentView(
        id=comment.id,
        workspace_id=workspace_id,
        project_id=project_id,
        task_id=task_id,
        author_user_id=user.id,
        author_display_name=user.display_name,
        body=comment.body,
        created_at=comment.created_at,
    )
