from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.v1.auth import current_user
from app.api.v1.documents import _search_documents
from app.core.config import settings
from app.db.session import get_db
from app.models.entities import (
    AssistantMemory,
    AssistantMessage,
    AuditEvent,
    ContextItem,
    DecisionRecord,
    Project,
    TaskItem,
    TelegramChat,
    TelegramMessage,
    User,
    WorkspaceMember,
)
from app.services.context_retrieval import relevance_score
from app.services.reasoning import (
    NativeTaskDraft,
    ReasoningProviderLimit,
    ReasoningUnavailable,
    propose_native_tasks,
    reason_over_context,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/assistant", tags=["workspace assistant"])


class AssistantChatCreate(BaseModel):
    prompt: str = Field(min_length=3, max_length=3000)
    project_id: UUID | None = None
    provider: str | None = Field(
        default=None, pattern="^(openai|anthropic|gemini|groq|xai|cloudflare)$"
    )

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Ask a question with at least three characters")
        return value


class MemoryCreate(BaseModel):
    content: str = Field(min_length=3, max_length=1000)
    project_id: UUID | None = None

    @field_validator("content")
    @classmethod
    def memory_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Memory must contain at least three characters")
        return value


class CitationView(BaseModel):
    id: UUID
    title: str
    provider: str
    source_url: str | None


class AssistantMessageView(BaseModel):
    id: UUID
    role: str
    content: str
    author_display_name: str | None
    provider: str | None
    model: str | None
    citations: list[CitationView]
    evidence_assessment: dict | None
    usage: dict[str, int] | None
    created_at: datetime


class AssistantMemoryView(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    content: str
    created_by_name: str
    created_at: datetime


class TaskProposalCreate(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000)
    provider: str | None = Field(
        default=None, pattern="^(openai|anthropic|gemini|groq|xai|cloudflare)$"
    )

    @field_validator("prompt")
    @classmethod
    def task_prompt_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Describe the work you want broken into tasks")
        return value


class SuggestedTaskView(BaseModel):
    title: str
    description: str
    priority: str
    labels: list[str]
    citations: list[CitationView]


class TaskProposalView(BaseModel):
    provider: str
    model: str
    summary: str
    needs_more_context: bool
    tasks: list[SuggestedTaskView]
    usage: dict[str, int] | None


def _workspace_role(db: Session, workspace_id: UUID, user_id: UUID, *, write: bool = False) -> str:
    role = db.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if write and role == "viewer":
        raise HTTPException(status_code=403, detail="Viewer access is read-only")
    return role


def _validate_project(db: Session, workspace_id: UUID, project_id: UUID | None) -> None:
    if project_id is None:
        return
    exists = db.scalar(
        select(Project.id).where(Project.id == project_id, Project.workspace_id == workspace_id)
    )
    if exists is None:
        raise HTTPException(status_code=404, detail="Project not found in this workspace")


def _memory_view(db: Session, memory: AssistantMemory) -> AssistantMemoryView:
    creator = db.scalar(select(User.display_name).where(User.id == memory.created_by))
    return AssistantMemoryView(
        id=memory.id,
        workspace_id=memory.workspace_id,
        project_id=memory.project_id,
        content=memory.content,
        created_by_name=creator or "Former member",
        created_at=memory.created_at,
    )


def _message_view(db: Session, message: AssistantMessage) -> AssistantMessageView:
    author = (
        db.scalar(select(User.display_name).where(User.id == message.author_user_id))
        if message.author_user_id
        else None
    )
    return AssistantMessageView(
        id=message.id,
        role=message.role,
        content=message.content,
        author_display_name=author,
        provider=message.provider,
        model=message.model,
        citations=[CitationView.model_validate(item) for item in (message.citations_json or [])],
        evidence_assessment=message.evidence_json,
        usage=message.usage_json,
        created_at=message.created_at,
    )


def _source(
    identifier: UUID,
    provider: str,
    title: str,
    content: str,
    source_url: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=identifier,
        provider=provider,
        title=title,
        content_text=content[:5000],
        source_url=source_url,
    )


def _workspace_evidence(
    db: Session,
    workspace_id: UUID,
    prompt: str,
    project_id: UUID | None,
) -> list[SimpleNamespace]:
    sources: list[SimpleNamespace] = []
    seen: set[UUID] = set()
    docs, document_sources = _search_documents(db, workspace_id, prompt, project_id, 6)
    for doc, source in zip(docs, document_sources, strict=True):
        if doc.document_id not in seen:
            sources.append(source)
            seen.add(doc.document_id)

    projects_query = select(Project).where(Project.workspace_id == workspace_id)
    if project_id is not None:
        projects_query = projects_query.where(Project.id == project_id)
    projects = db.scalars(projects_query.order_by(Project.updated_at.desc()).limit(8)).all()
    for project in projects:
        content = (
            f"Project: {project.name}\nDescription: {project.description}\nStatus: {project.status}"
        )
        sources.append(_source(project.id, "native_project", project.name, content))
        seen.add(project.id)

    tasks_query = select(TaskItem).where(TaskItem.workspace_id == workspace_id)
    if project_id is not None:
        tasks_query = tasks_query.where(TaskItem.project_id == project_id)
    tasks = db.scalars(tasks_query.order_by(TaskItem.updated_at.desc()).limit(80)).all()
    task_rows = []
    for task in tasks:
        project_name = next(
            (project.name for project in projects if project.id == task.project_id), None
        )
        content = (
            f"Project: {project_name or 'Workspace project'}\nTask: {task.title}\n"
            f"Description: {task.description}\nStatus: {task.status}\nPriority: {task.priority}\n"
            f"Due date: {task.due_date.isoformat() if task.due_date else 'Not set'}"
        )
        score = relevance_score(prompt, content)
        task_rows.append((score, task.updated_at, task, content))
    task_rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    for score, _, task, content in task_rows[:8]:
        if score > 0 or len(sources) < 3:
            sources.append(_source(task.id, "native_task", task.title, content))
            seen.add(task.id)

    decisions_query = select(DecisionRecord).where(DecisionRecord.workspace_id == workspace_id)
    if project_id is not None:
        decisions_query = decisions_query.where(DecisionRecord.project_id == project_id)
    decisions = db.scalars(
        decisions_query.order_by(DecisionRecord.updated_at.desc()).limit(40)
    ).all()
    decision_rows = []
    for decision in decisions:
        alternatives = "; ".join(decision.alternatives or [])
        content = (
            f"Decision: {decision.title}\nDescription: {decision.description}\n"
            f"Reasoning: {decision.reasoning}\nAlternatives: {alternatives}\n"
            f"Status: {decision.status}\nDate: {decision.decision_date.isoformat()}"
        )
        decision_rows.append(
            (relevance_score(prompt, content), decision.updated_at, decision, content)
        )
    decision_rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    for score, _, decision, content in decision_rows[:6]:
        if score > 0 or len(sources) < 4:
            sources.append(_source(decision.id, "native_decision", decision.title, content))
            seen.add(decision.id)

    external_query = select(ContextItem).where(ContextItem.workspace_id == workspace_id)
    external = db.scalars(external_query.order_by(ContextItem.fetched_at.desc()).limit(40)).all()
    external_rows = [
        (relevance_score(prompt, f"{item.title}\n{item.content_text}"), item) for item in external
    ]
    external_rows.sort(key=lambda item: (item[0], item[1].fetched_at), reverse=True)
    for score, item in external_rows[:6]:
        if score > 0 and item.id not in seen:
            sources.append(
                _source(
                    item.id,
                    item.source_type.split(":", 1)[0],
                    item.title,
                    item.content_text,
                    item.source_url,
                )
            )
            seen.add(item.id)

    telegram_query = (
        select(TelegramMessage, TelegramChat)
        .join(TelegramChat, TelegramChat.id == TelegramMessage.chat_id)
        .where(
            TelegramMessage.workspace_id == workspace_id,
            TelegramChat.status.in_(["connected", "paused"]),
        )
    )
    if project_id is not None:
        telegram_query = telegram_query.where(
            or_(TelegramMessage.project_id.is_(None), TelegramMessage.project_id == project_id)
        )
    telegram_rows = db.execute(
        telegram_query.order_by(TelegramMessage.message_at.desc()).limit(120)
    ).all()
    ranked_telegram = []
    for message, chat in telegram_rows:
        content = (
            f"Telegram group: {chat.title}\nSender: {message.sender_name}\n"
            f"Sent: {message.message_at.isoformat()}\nMessage: {message.content}"
        )
        ranked_telegram.append((relevance_score(prompt, content), message, chat, content))
    ranked_telegram.sort(key=lambda item: (item[0], item[1].message_at), reverse=True)
    for score, message, chat, content in ranked_telegram[:6]:
        if score > 0 and message.id not in seen:
            sources.append(
                _source(
                    message.id,
                    "telegram",
                    f"{chat.title} · {message.sender_name}",
                    content,
                    message.source_url,
                )
            )
            seen.add(message.id)

    return sources[:24]


@router.get("/messages", response_model=list[AssistantMessageView])
def list_messages(
    workspace_id: UUID,
    limit: int = Query(default=100, ge=1, le=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[AssistantMessageView]:
    _workspace_role(db, workspace_id, user.id)
    rows = db.scalars(
        select(AssistantMessage)
        .where(AssistantMessage.workspace_id == workspace_id)
        .order_by(AssistantMessage.created_at.desc(), AssistantMessage.id.desc())
        .limit(limit)
    ).all()
    return [_message_view(db, row) for row in reversed(rows)]


@router.post("/projects/{project_id}/task-proposals", response_model=TaskProposalView)
async def suggest_project_tasks(
    workspace_id: UUID,
    project_id: UUID,
    payload: TaskProposalCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TaskProposalView:
    _workspace_role(db, workspace_id, user.id)
    _validate_project(db, workspace_id, project_id)
    provider = payload.provider or settings.active_reasoning_provider
    sources = _workspace_evidence(db, workspace_id, payload.prompt, project_id)
    memories = db.scalars(
        select(AssistantMemory)
        .where(
            AssistantMemory.workspace_id == workspace_id,
            (AssistantMemory.project_id == project_id) | (AssistantMemory.project_id.is_(None)),
        )
        .order_by(AssistantMemory.created_at.desc())
        .limit(12)
    ).all()
    sources.extend(
        _source(memory.id, "workspace_memory", "Saved workspace memory", memory.content)
        for memory in memories
    )
    try:
        result = await propose_native_tasks(payload.prompt, sources, provider=provider)
    except ReasoningProviderLimit as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ReasoningUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "No configured reasoning model is available. Configure a provider and model "
                "on the API."
            ),
        )

    source_by_id = {str(source.id): source for source in sources}
    drafts: list[SuggestedTaskView] = []
    for draft in result["tasks"]:
        parsed = NativeTaskDraft.model_validate(draft)
        citations = [
            CitationView(
                id=citation_id,
                title=source_by_id[citation_id].title,
                provider=source_by_id[citation_id].provider,
                source_url=source_by_id[citation_id].source_url,
            )
            for citation_id in parsed.citations
            if citation_id in source_by_id
        ]
        if not citations:
            raise HTTPException(
                status_code=502, detail="The task proposal returned no valid evidence."
            )
        drafts.append(
            SuggestedTaskView(
                title=parsed.title,
                description=parsed.description,
                priority=parsed.priority,
                labels=parsed.labels,
                citations=citations,
            )
        )

    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="assistant.task_proposals_generated",
            object_type="task_proposal",
            details_json={
                "provider": result["provider"],
                "model": result["model"],
                "source_count": len(sources),
                "proposal_count": len(drafts),
                "usage": result.get("usage"),
            },
        )
    )
    db.commit()
    return TaskProposalView(
        provider=result["provider"],
        model=result["model"],
        summary=result["summary"],
        needs_more_context=result["needs_more_context"],
        tasks=drafts,
        usage=result.get("usage"),
    )


@router.post("/chat", response_model=AssistantMessageView, status_code=status.HTTP_201_CREATED)
async def send_message(
    workspace_id: UUID,
    payload: AssistantChatCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AssistantMessageView:
    _workspace_role(db, workspace_id, user.id)
    _validate_project(db, workspace_id, payload.project_id)
    provider = payload.provider or settings.active_reasoning_provider
    credentials = settings.reasoning_credentials(provider)
    if credentials is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "No configured reasoning model is available. Configure a provider and model "
                "on the API."
            ),
        )

    history_rows = db.scalars(
        select(AssistantMessage)
        .where(AssistantMessage.workspace_id == workspace_id)
        .order_by(AssistantMessage.created_at.desc(), AssistantMessage.id.desc())
        .limit(12)
    ).all()
    history = [{"role": row.role, "content": row.content[:1500]} for row in reversed(history_rows)]
    sources = _workspace_evidence(db, workspace_id, payload.prompt, payload.project_id)
    memories_query = select(AssistantMemory).where(AssistantMemory.workspace_id == workspace_id)
    if payload.project_id is not None:
        memories_query = memories_query.where(
            (AssistantMemory.project_id == payload.project_id)
            | (AssistantMemory.project_id.is_(None))
        )
    memories = db.scalars(
        memories_query.order_by(AssistantMemory.created_at.desc()).limit(24)
    ).all()
    for memory in memories:
        sources.append(
            _source(memory.id, "workspace_memory", "Saved workspace memory", memory.content)
        )

    try:
        answer = await reason_over_context(
            SimpleNamespace(prompt=payload.prompt),
            sources,
            provider=provider,
            history=history,
            allow_empty_context=True,
        )
    except ReasoningProviderLimit as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ReasoningUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if answer is None:
        raise HTTPException(status_code=503, detail="The selected AI provider is not configured.")

    citation_sources = {str(source.id): source for source in sources}
    citations = [
        {
            "id": citation_id,
            "title": citation_sources[citation_id].title,
            "provider": citation_sources[citation_id].provider,
            "source_url": citation_sources[citation_id].source_url,
        }
        for citation_id in answer["citation_ids"]
        if citation_id in citation_sources
    ]
    now = datetime.now(UTC)
    db.add(
        AssistantMessage(
            workspace_id=workspace_id,
            author_user_id=user.id,
            role="user",
            content=payload.prompt,
            citations_json=[],
            created_at=now,
        )
    )
    assistant_message = AssistantMessage(
        workspace_id=workspace_id,
        role="assistant",
        content=answer["answer"],
        provider=answer["provider"],
        model=answer["model"],
        citations_json=citations,
        usage_json=answer.get("usage"),
        evidence_json=answer.get("evidence_assessment"),
        created_at=now + timedelta(microseconds=1),
    )
    db.add(assistant_message)
    db.flush()
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="assistant.message_created",
            object_type="assistant_message",
            object_id=assistant_message.id,
            details_json={
                "provider": answer["provider"],
                "source_count": len(sources),
                "usage": answer.get("usage"),
            },
        )
    )
    db.commit()
    db.refresh(assistant_message)
    return _message_view(db, assistant_message)


@router.get("/memories", response_model=list[AssistantMemoryView])
def list_memories(
    workspace_id: UUID,
    project_id: UUID | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[AssistantMemoryView]:
    _workspace_role(db, workspace_id, user.id)
    statement = select(AssistantMemory).where(AssistantMemory.workspace_id == workspace_id)
    if project_id is not None:
        statement = statement.where(
            (AssistantMemory.project_id == project_id) | (AssistantMemory.project_id.is_(None))
        )
    memories = db.scalars(statement.order_by(AssistantMemory.created_at.desc())).all()
    return [_memory_view(db, memory) for memory in memories]


@router.post("/memories", response_model=AssistantMemoryView, status_code=status.HTTP_201_CREATED)
def create_memory(
    workspace_id: UUID,
    payload: MemoryCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AssistantMemoryView:
    _workspace_role(db, workspace_id, user.id, write=True)
    _validate_project(db, workspace_id, payload.project_id)
    existing_count = len(
        db.scalars(
            select(AssistantMemory.id).where(AssistantMemory.workspace_id == workspace_id)
        ).all()
    )
    if existing_count >= 100:
        raise HTTPException(
            status_code=409, detail="This workspace has reached its 100-memory limit."
        )
    memory = AssistantMemory(
        workspace_id=workspace_id,
        project_id=payload.project_id,
        created_by=user.id,
        content=payload.content,
    )
    db.add(memory)
    db.flush()
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            project_id=payload.project_id,
            actor_user_id=user.id,
            event_type="assistant.memory_created",
            object_type="assistant_memory",
            object_id=memory.id,
            details_json={"content_length": len(payload.content)},
        )
    )
    db.commit()
    db.refresh(memory)
    return _memory_view(db, memory)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(
    workspace_id: UUID,
    memory_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    _workspace_role(db, workspace_id, user.id, write=True)
    memory = db.scalar(
        select(AssistantMemory).where(
            AssistantMemory.id == memory_id,
            AssistantMemory.workspace_id == workspace_id,
        )
    )
    if memory is None:
        raise HTTPException(status_code=404, detail="Saved memory not found")
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            project_id=memory.project_id,
            actor_user_id=user.id,
            event_type="assistant.memory_deleted",
            object_type="assistant_memory",
            object_id=memory.id,
            details_json={},
        )
    )
    db.delete(memory)
    db.commit()
