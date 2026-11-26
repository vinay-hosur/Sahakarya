import hashlib
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.auth import current_user
from app.core.config import settings
from app.core.secret_store import SecretStoreUnavailable, decrypt_secret
from app.db.session import get_db
from app.models.entities import (
    AuditEvent,
    ContextItem,
    Integration,
    RequestContext,
    User,
    UserRequest,
    WorkspaceMember,
)
from app.services.context_retrieval import (
    RetrievedContext,
    relevance_score,
    retrieve_from_integration,
)
from app.services.reasoning import ReasoningUnavailable, reason_over_context

logger = logging.getLogger("sahakarya.requests")
router = APIRouter(prefix="/workspaces/{workspace_id}/requests", tags=["context requests"])


class RequestCreate(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000)
    provider: str | None = Field(default=None, pattern="^(openai|anthropic|gemini)$")

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Ask a question with at least three characters")
        return value


class ContextSource(BaseModel):
    id: UUID
    provider: str
    tool_name: str
    title: str
    content_text: str
    source_url: str | None
    author: str | None
    occurred_at: datetime | None
    relevance_score: float


class SuggestedAction(BaseModel):
    title: str
    description: str


class ReasoningResult(BaseModel):
    provider: str
    model: str
    answer: str
    citation_ids: list[UUID]
    needs_more_context: bool
    suggested_action: SuggestedAction | None


class RequestResult(BaseModel):
    request_id: UUID
    workspace_id: UUID
    prompt: str
    status: str
    created_at: datetime
    completed_at: datetime | None
    sources: list[ContextSource]
    detail: str | None = None
    reasoning: ReasoningResult | None = None


class RequestSummary(BaseModel):
    request_id: UUID
    prompt: str
    status: str
    created_at: datetime
    completed_at: datetime | None
    source_count: int


def _require_workspace(db: Session, workspace_id: UUID, user_id: UUID) -> None:
    member = db.scalar(
        select(WorkspaceMember.workspace_id).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")


def _request_sources(db: Session, request_id: UUID) -> list[ContextSource]:
    rows = db.execute(
        select(ContextItem, RequestContext.relevance_score)
        .join(RequestContext, RequestContext.context_item_id == ContextItem.id)
        .where(RequestContext.request_id == request_id)
        .order_by(RequestContext.rank)
    ).all()
    sources: list[ContextSource] = []
    for context_item, score in rows:
        metadata = context_item.metadata_json or {}
        sources.append(
            ContextSource(
                id=context_item.id,
                provider=metadata.get("provider", context_item.source_type.split(":", 1)[0]),
                tool_name=metadata.get("tool_name", "unknown"),
                title=context_item.title,
                content_text=context_item.content_text,
                source_url=context_item.source_url,
                author=context_item.author,
                occurred_at=context_item.occurred_at,
                relevance_score=score or 0.0,
            )
        )
    return sources


def _result(db: Session, request: UserRequest, detail: str | None = None) -> RequestResult:
    sources = _request_sources(db, request.id)
    if detail is None:
        if request.status == "needs_sources":
            detail = "Connect a verified MCP source to search team context."
        elif not sources and request.status == "failed":
            detail = (
                "No source returned evidence. Recheck the connected provider "
                "and its search permissions."
            )
        elif not sources:
            detail = "No matching evidence was returned by the connected sources."
        elif request.reasoning_json is None and not settings.reasoning_configured:
            detail = (
                "Evidence was retrieved. Configure OPENAI_API_KEY and OPENAI_MODEL "
                "for grounded synthesis."
            )
        elif request.reasoning_json is None:
            detail = "No grounded answer was validated for this search."
    return RequestResult(
        request_id=request.id,
        workspace_id=request.workspace_id,
        prompt=request.prompt,
        status=request.status,
        created_at=request.created_at,
        completed_at=request.completed_at,
        sources=sources,
        detail=detail,
        reasoning=ReasoningResult.model_validate(request.reasoning_json)
        if request.reasoning_json
        else None,
    )


def _rank_key(item: RetrievedContext, prompt: str) -> tuple[float, float, str]:
    score = relevance_score(prompt, item.content_text)
    occurred = item.occurred_at.timestamp() if item.occurred_at else 0.0
    return (-score, -occurred, item.external_id)


def _save_context(
    db: Session,
    integration: Integration,
    request: UserRequest,
    item: RetrievedContext,
    rank: int,
    score: float,
) -> ContextItem:
    context_item = db.scalar(
        select(ContextItem).where(
            ContextItem.integration_id == integration.id,
            ContextItem.external_id == item.external_id,
        )
    )
    content_hash = hashlib.sha256(item.content_text.encode("utf-8")).hexdigest()
    if context_item is None:
        context_item = ContextItem(
            workspace_id=request.workspace_id,
            integration_id=integration.id,
            source_type=f"{integration.provider}:{item.tool_name}",
            external_id=item.external_id,
            source_url=item.source_url,
            title=item.title,
            content_text=item.content_text,
            author=item.author,
            occurred_at=item.occurred_at,
            content_hash=content_hash,
            metadata_json=item.metadata,
        )
        db.add(context_item)
        db.flush()
    else:
        context_item.source_type = f"{integration.provider}:{item.tool_name}"
        context_item.source_url = item.source_url
        context_item.title = item.title
        context_item.content_text = item.content_text
        context_item.author = item.author
        context_item.occurred_at = item.occurred_at
        context_item.content_hash = content_hash
        context_item.metadata_json = item.metadata

    db.add(
        RequestContext(
            request_id=request.id,
            context_item_id=context_item.id,
            rank=rank,
            relevance_score=score,
            reason="lexical_overlap",
        )
    )
    return context_item


@router.get("", response_model=list[RequestSummary])
def list_requests(
    workspace_id: UUID,
    limit: int = Query(default=10, ge=1, le=50),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[RequestSummary]:
    _require_workspace(db, workspace_id, user.id)
    requests = db.scalars(
        select(UserRequest)
        .where(UserRequest.workspace_id == workspace_id)
        .order_by(UserRequest.created_at.desc())
        .limit(limit)
    ).all()
    return [
        RequestSummary(
            request_id=request.id,
            prompt=request.prompt,
            status=request.status,
            created_at=request.created_at,
            completed_at=request.completed_at,
            source_count=len(_request_sources(db, request.id)),
        )
        for request in requests
    ]


@router.post("", response_model=RequestResult, status_code=status.HTTP_201_CREATED)
async def create_request(
    workspace_id: UUID,
    payload: RequestCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RequestResult:
    _require_workspace(db, workspace_id, user.id)
    request = UserRequest(
        workspace_id=workspace_id,
        user_id=user.id,
        prompt=payload.prompt,
        status="running",
    )
    db.add(request)
    db.flush()
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="context_request.started",
            object_type="user_request",
            object_id=request.id,
            details_json={"prompt_length": len(payload.prompt)},
        )
    )
    db.commit()
    db.refresh(request)

    integrations = db.scalars(
        select(Integration)
        .where(
            Integration.workspace_id == workspace_id,
            Integration.status == "connected",
            Integration.provider == "github",
        )
        .order_by(Integration.provider)
    ).all()
    if not integrations:
        request.status = "needs_sources"
        request.completed_at = datetime.now(UTC)
        db.add(
            AuditEvent(
                workspace_id=workspace_id,
                actor_user_id=user.id,
                event_type="context_request.completed",
                object_type="user_request",
                object_id=request.id,
                details_json={"status": "needs_sources", "source_count": 0},
            )
        )
        db.commit()
        db.refresh(request)
        return _result(db, request, "Connect a verified MCP source to search team context.")

    retrieved: list[tuple[Integration, RetrievedContext]] = []
    failed_providers: list[str] = []
    for integration in integrations:
        if not integration.secret_ciphertext:
            integration.status = "error"
            integration.last_error = "No encrypted provider credential is available"
            integration.last_checked_at = datetime.now(UTC)
            failed_providers.append(integration.provider)
            continue
        try:
            access_token = decrypt_secret(integration.secret_ciphertext)
            items = await retrieve_from_integration(integration, access_token, payload.prompt)
            retrieved.extend((integration, item) for item in items)
            integration.status = "connected"
            integration.last_error = None
            integration.last_checked_at = datetime.now(UTC)
        except SecretStoreUnavailable:
            integration.status = "error"
            integration.last_error = "Stored provider credential could not be used"
            integration.last_checked_at = datetime.now(UTC)
            failed_providers.append(integration.provider)
            logger.warning(
                "MCP context read failed provider=%s error=secret_store", integration.provider
            )
        except Exception as exc:
            integration.status = "error"
            integration.last_error = "MCP context read failed; recheck provider authorization"
            integration.last_checked_at = datetime.now(UTC)
            failed_providers.append(integration.provider)
            logger.warning(
                "MCP context read failed provider=%s error_type=%s",
                integration.provider,
                type(exc).__name__,
            )

    deduped: dict[tuple[UUID, str], tuple[Integration, RetrievedContext]] = {}
    for integration, item in retrieved:
        deduped[(integration.id, item.external_id)] = (integration, item)
    ranked = sorted(deduped.values(), key=lambda pair: _rank_key(pair[1], payload.prompt))[:20]
    sources: list[ContextSource] = []
    total_characters = 0
    for rank, (integration, item) in enumerate(ranked, start=1):
        if total_characters + len(item.content_text) > 50000:
            break
        total_characters += len(item.content_text)
        score = relevance_score(payload.prompt, item.content_text)
        context_item = _save_context(db, integration, request, item, rank, score)
        sources.append(
            ContextSource(
                id=context_item.id,
                provider=integration.provider,
                tool_name=item.tool_name,
                title=context_item.title,
                content_text=context_item.content_text,
                source_url=context_item.source_url,
                author=context_item.author,
                occurred_at=context_item.occurred_at,
                relevance_score=score,
            )
        )

    reasoning_error = False
    if sources:
        try:
            request.reasoning_json = await reason_over_context(
                request, sources, provider=payload.provider
            )
        except ReasoningUnavailable as exc:
            reasoning_error = True
            logger.warning("grounded reasoning unavailable error_type=%s", type(exc).__name__)
        except Exception as exc:
            reasoning_error = True
            logger.warning("grounded reasoning failed error_type=%s", type(exc).__name__)

    request.status = "completed" if not failed_providers or sources else "failed"
    request.completed_at = datetime.now(UTC)
    detail = None
    if not sources and failed_providers:
        detail = (
            "No source returned evidence. Recheck the connected provider "
            "and its search permissions."
        )
    elif not sources:
        detail = "No matching evidence was returned by the connected sources."
    elif reasoning_error:
        detail = "Evidence was retrieved, but grounded reasoning could not be validated."
    elif request.reasoning_json is None:
        detail = (
            "Evidence was retrieved. Configure OPENAI_API_KEY and OPENAI_MODEL "
            "for grounded synthesis."
        )
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="context_request.completed",
            object_type="user_request",
            object_id=request.id,
            details_json={
                "status": request.status,
                "source_count": len(sources),
                "providers": sorted({integration.provider for integration, _ in retrieved}),
                "failed_providers": failed_providers,
            },
        )
    )
    db.commit()
    db.refresh(request)
    return RequestResult(
        request_id=request.id,
        workspace_id=request.workspace_id,
        prompt=request.prompt,
        status=request.status,
        created_at=request.created_at,
        completed_at=request.completed_at,
        sources=sources,
        detail=detail,
        reasoning=ReasoningResult.model_validate(request.reasoning_json)
        if request.reasoning_json
        else None,
    )


@router.get("/{request_id}", response_model=RequestResult)
def get_request(
    workspace_id: UUID,
    request_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RequestResult:
    _require_workspace(db, workspace_id, user.id)
    request = db.scalar(
        select(UserRequest).where(
            UserRequest.id == request_id,
            UserRequest.workspace_id == workspace_id,
        )
    )
    if request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    return _result(db, request)
