from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.auth import current_user
from app.db.session import get_db
from app.models.entities import DecisionRecord, Project, TaskItem, User, WorkspaceMember

router = APIRouter(prefix="/workspaces/{workspace_id}/projects", tags=["project intelligence"])


class OverviewTask(BaseModel):
    id: UUID
    title: str
    status: str
    priority: str
    due_date: date | None


class OverviewDecision(BaseModel):
    id: UUID
    title: str
    status: str
    decision_date: date


class ProjectOverview(BaseModel):
    project_id: UUID
    generated_at: datetime
    total_tasks: int
    status_counts: dict[str, int]
    overdue_tasks: list[OverviewTask]
    due_soon_tasks: list[OverviewTask]
    blocked_tasks: list[OverviewTask]
    high_priority_tasks: list[OverviewTask]
    recent_decisions: list[OverviewDecision]


def _member(db: Session, workspace_id: UUID, user_id: UUID) -> None:
    exists = db.scalar(
        select(WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if exists is None:
        raise HTTPException(status_code=404, detail="Workspace not found")


def _overview_task(row: TaskItem) -> OverviewTask:
    return OverviewTask(
        id=row.id,
        title=row.title,
        status=row.status,
        priority=row.priority,
        due_date=row.due_date,
    )


@router.get("/{project_id}/overview", response_model=ProjectOverview)
def project_overview(
    workspace_id: UUID,
    project_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProjectOverview:
    _member(db, workspace_id, user.id)
    project_exists = db.scalar(
        select(Project.id).where(
            Project.id == project_id,
            Project.workspace_id == workspace_id,
        )
    )
    if project_exists is None:
        raise HTTPException(status_code=404, detail="Project not found")

    status_counts = {status: 0 for status in ("todo", "in_progress", "blocked", "done")}
    grouped_counts = db.execute(
        select(TaskItem.status, func.count(TaskItem.id))
        .where(TaskItem.workspace_id == workspace_id, TaskItem.project_id == project_id)
        .group_by(TaskItem.status)
    ).all()
    for task_status, count in grouped_counts:
        status_counts[task_status] = count

    open_tasks = select(TaskItem).where(
        TaskItem.workspace_id == workspace_id,
        TaskItem.project_id == project_id,
        TaskItem.status != "done",
    )
    today = date.today()
    due_soon_end = today + timedelta(days=7)
    overdue = db.scalars(
        open_tasks.where(TaskItem.due_date < today)
        .order_by(TaskItem.due_date, TaskItem.id)
        .limit(6)
    ).all()
    due_soon = db.scalars(
        open_tasks.where(TaskItem.due_date >= today, TaskItem.due_date <= due_soon_end)
        .order_by(TaskItem.due_date, TaskItem.id)
        .limit(6)
    ).all()
    blocked = db.scalars(
        select(TaskItem)
        .where(
            TaskItem.workspace_id == workspace_id,
            TaskItem.project_id == project_id,
            TaskItem.status == "blocked",
        )
        .order_by(TaskItem.updated_at.desc())
        .limit(6)
    ).all()
    high_priority = db.scalars(
        open_tasks.where(TaskItem.priority.in_(("high", "urgent")))
        .order_by(TaskItem.priority.desc(), TaskItem.due_date, TaskItem.updated_at.desc())
        .limit(6)
    ).all()
    decisions = db.scalars(
        select(DecisionRecord)
        .where(
            DecisionRecord.workspace_id == workspace_id,
            DecisionRecord.project_id == project_id,
        )
        .order_by(DecisionRecord.updated_at.desc())
        .limit(5)
    ).all()
    return ProjectOverview(
        project_id=project_id,
        generated_at=datetime.now(UTC),
        total_tasks=sum(status_counts.values()),
        status_counts=status_counts,
        overdue_tasks=[_overview_task(row) for row in overdue],
        due_soon_tasks=[_overview_task(row) for row in due_soon],
        blocked_tasks=[_overview_task(row) for row in blocked],
        high_priority_tasks=[_overview_task(row) for row in high_priority],
        recent_decisions=[
            OverviewDecision(
                id=decision.id,
                title=decision.title,
                status=decision.status,
                decision_date=decision.decision_date,
            )
            for decision in decisions
        ],
    )
