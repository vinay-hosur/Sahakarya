import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx2
from fastapi import APIRouter, Depends, HTTPException, status
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.auth import current_user
from app.api.v1.integrations import PROVIDERS
from app.core.secret_store import SecretStoreUnavailable, decrypt_secret
from app.db.session import get_db
from app.models.entities import (
    ActionExecution,
    ActionProposal,
    AuditEvent,
    ContextItem,
    Integration,
    RequestContext,
    User,
    UserRequest,
    WorkspaceMember,
)
from app.services.context_retrieval import normalize_mcp_result

logger = logging.getLogger("sahakarya.actions")
router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["reviewed actions"])
ISSUE_REFERENCE_PREFIX = "sahakarya-action-"
REPO_PART = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class ProposalFields(BaseModel):
    repository: str = Field(min_length=3, max_length=201)
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=20000)

    @field_validator("repository", "title", "description")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("repository")
    @classmethod
    def repository_is_owner_slash_name(cls, value: str) -> str:
        parts = value.strip().split("/")
        if len(parts) != 2 or not all(REPO_PART.fullmatch(part) for part in parts):
            raise ValueError("Repository must use owner/repository format")
        return "/".join(parts)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("Issue title cannot be blank")
        return value


class ProposalCreate(ProposalFields):
    source_context_ids: list[UUID] = Field(min_length=1, max_length=10)


class ProposalUpdate(ProposalFields):
    pass


class ProposalApproval(BaseModel):
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ActionExecutionView(BaseModel):
    status: str
    external_id: str | None
    external_url: str | None
    error_code: str | None
    completed_at: datetime | None


class ProposalView(BaseModel):
    id: UUID
    request_id: UUID
    workspace_id: UUID
    action_type: str
    target: dict
    payload: dict
    editable_description: str
    payload_hash: str
    status: str
    created_at: datetime
    approved_at: datetime | None
    execution: ActionExecutionView | None = None


def _require_workspace(db: Session, workspace_id: UUID, user_id: UUID) -> None:
    member = db.scalar(
        select(WorkspaceMember.workspace_id).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")


def _canonical_hash(target: dict, payload: dict) -> str:
    encoded = json.dumps(
        {"target": target, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _execution_for(db: Session, proposal_id: UUID) -> ActionExecution | None:
    return db.scalar(select(ActionExecution).where(ActionExecution.proposal_id == proposal_id))


def _proposal_view(db: Session, proposal: ActionProposal) -> ProposalView:
    execution = _execution_for(db, proposal.id)
    return ProposalView(
        id=proposal.id,
        request_id=proposal.request_id,
        workspace_id=proposal.workspace_id,
        action_type=proposal.action_type,
        target=proposal.target_json,
        payload=proposal.payload_json,
        editable_description=proposal.target_json.get("draft_description", ""),
        payload_hash=proposal.payload_hash,
        status=proposal.status,
        created_at=proposal.created_at,
        approved_at=proposal.approved_at,
        execution=(
            ActionExecutionView(
                status=execution.status,
                external_id=execution.external_id,
                external_url=execution.external_url,
                error_code=execution.error_code,
                completed_at=execution.completed_at,
            )
            if execution
            else None
        ),
    )


def _source_citation(source: ContextItem) -> str:
    title = source.title.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    if source.source_url:
        parsed = urlparse(source.source_url)
        if parsed.scheme == "https" and (
            parsed.hostname == "github.com"
            or parsed.hostname == "slack.com"
            or (parsed.hostname or "").endswith(".slack.com")
        ):
            return f"- [{title}]({source.source_url})"
    return f"- {title}"


def _issue_body(description: str, sources: list[ContextItem], reference: str) -> str:
    body_parts = [description.strip()] if description.strip() else []
    source_lines = "\n".join(_source_citation(source) for source in sources)
    body_parts.extend(["Context sources:", source_lines, f"Action reference: `{reference}`"])
    return "\n\n".join(body_parts)


def _context_sources(
    db: Session, workspace_id: UUID, request_id: UUID, source_ids: list[UUID]
) -> list[ContextItem]:
    rows = (
        db.execute(
            select(ContextItem)
            .join(RequestContext, RequestContext.context_item_id == ContextItem.id)
            .where(
                RequestContext.request_id == request_id,
                ContextItem.workspace_id == workspace_id,
                ContextItem.id.in_(source_ids),
            )
        )
        .scalars()
        .all()
    )
    source_by_id = {source.id: source for source in rows}
    if len(source_by_id) != len(set(source_ids)):
        raise HTTPException(
            status_code=422,
            detail="Every cited source must belong to this context request and workspace",
        )
    return [source_by_id[source_id] for source_id in source_ids]


def _github_integration(db: Session, workspace_id: UUID) -> Integration:
    github = db.scalar(
        select(Integration).where(
            Integration.workspace_id == workspace_id,
            Integration.provider == "github",
            Integration.status == "connected",
        )
    )
    if github is None or not github.secret_ciphertext:
        raise HTTPException(
            status_code=409, detail="Connect and verify GitHub before drafting an issue"
        )
    create_tool = next(
        (tool for tool in github.capabilities_json if tool.get("name") == "create_issue"), None
    )
    schema = (create_tool or {}).get("inputSchema") or {}
    properties = schema.get("properties") if isinstance(schema, dict) else None
    required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
    annotations = (create_tool or {}).get("annotations") or {}
    if (
        not isinstance(properties, dict)
        or not {"owner", "repo", "title"}.issubset(required)
        or annotations.get("readOnlyHint") is not False
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "The verified GitHub MCP server does not expose the supported create_issue action"
            ),
        )
    return github


@router.post("/requests/{request_id}/proposals", response_model=ProposalView, status_code=201)
def create_proposal(
    workspace_id: UUID,
    request_id: UUID,
    payload: ProposalCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProposalView:
    _require_workspace(db, workspace_id, user.id)
    request = db.scalar(
        select(UserRequest).where(
            UserRequest.id == request_id,
            UserRequest.workspace_id == workspace_id,
            UserRequest.status == "completed",
        )
    )
    if request is None:
        raise HTTPException(status_code=404, detail="Completed context request not found")

    ordered_sources = _context_sources(db, workspace_id, request_id, payload.source_context_ids)
    _github_integration(db, workspace_id)

    owner, repo = payload.repository.split("/", maxsplit=1)
    proposal_id = uuid4()
    reference = f"{ISSUE_REFERENCE_PREFIX}{proposal_id.hex}"
    description = payload.description.strip()
    action_body = _issue_body(description, ordered_sources, reference)
    target = {
        "provider": "github",
        "repository": payload.repository,
        "owner": owner,
        "repo": repo,
        "source_context_ids": [str(source.id) for source in ordered_sources],
        "action_reference": reference,
        "draft_description": description,
    }
    action_payload = {"title": payload.title, "body": action_body}
    proposal = ActionProposal(
        id=proposal_id,
        request_id=request_id,
        workspace_id=workspace_id,
        action_type="github.create_issue",
        target_json=target,
        payload_json=action_payload,
        payload_hash=_canonical_hash(target, action_payload),
        status="draft",
        created_by=user.id,
    )
    db.add(proposal)
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="action_proposal.created",
            object_type="action_proposal",
            object_id=proposal.id,
            details_json={
                "action_type": proposal.action_type,
                "payload_hash": proposal.payload_hash,
            },
        )
    )
    db.commit()
    db.refresh(proposal)
    return _proposal_view(db, proposal)


@router.get("/requests/{request_id}/proposals", response_model=list[ProposalView])
def list_request_proposals(
    workspace_id: UUID,
    request_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[ProposalView]:
    _require_workspace(db, workspace_id, user.id)
    request = db.scalar(
        select(UserRequest.id).where(
            UserRequest.id == request_id,
            UserRequest.workspace_id == workspace_id,
        )
    )
    if request is None:
        raise HTTPException(status_code=404, detail="Context request not found")
    proposals = db.scalars(
        select(ActionProposal)
        .where(
            ActionProposal.request_id == request_id,
            ActionProposal.workspace_id == workspace_id,
        )
        .order_by(ActionProposal.created_at.desc())
        .limit(10)
    ).all()
    return [_proposal_view(db, proposal) for proposal in proposals]


@router.get("/proposals/{proposal_id}", response_model=ProposalView)
def get_proposal(
    workspace_id: UUID,
    proposal_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProposalView:
    _require_workspace(db, workspace_id, user.id)
    proposal = db.scalar(
        select(ActionProposal).where(
            ActionProposal.id == proposal_id,
            ActionProposal.workspace_id == workspace_id,
        )
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="Action proposal not found")
    return _proposal_view(db, proposal)


@router.patch("/proposals/{proposal_id}", response_model=ProposalView)
def update_proposal(
    workspace_id: UUID,
    proposal_id: UUID,
    payload: ProposalUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProposalView:
    _require_workspace(db, workspace_id, user.id)
    proposal = db.scalar(
        select(ActionProposal).where(
            ActionProposal.id == proposal_id,
            ActionProposal.workspace_id == workspace_id,
        )
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="Action proposal not found")
    if proposal.status != "draft":
        raise HTTPException(status_code=409, detail="Only an unapproved draft can be edited")
    request = db.scalar(
        select(UserRequest).where(
            UserRequest.id == proposal.request_id,
            UserRequest.workspace_id == workspace_id,
            UserRequest.status == "completed",
        )
    )
    if request is None:
        raise HTTPException(
            status_code=409, detail="Original context request is no longer available"
        )
    source_ids = [UUID(source_id) for source_id in proposal.target_json["source_context_ids"]]
    sources = _context_sources(db, workspace_id, proposal.request_id, source_ids)
    _github_integration(db, workspace_id)
    previous_hash = proposal.payload_hash
    owner, repo = payload.repository.split("/", maxsplit=1)
    target = {
        **proposal.target_json,
        "repository": payload.repository,
        "owner": owner,
        "repo": repo,
        "draft_description": payload.description,
    }
    action_payload = {
        "title": payload.title,
        "body": _issue_body(payload.description, sources, target["action_reference"]),
    }
    proposal.target_json = target
    proposal.payload_json = action_payload
    proposal.payload_hash = _canonical_hash(target, action_payload)
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="action_proposal.updated",
            object_type="action_proposal",
            object_id=proposal.id,
            details_json={
                "previous_hash": previous_hash,
                "payload_hash": proposal.payload_hash,
            },
        )
    )
    db.commit()
    db.refresh(proposal)
    return _proposal_view(db, proposal)


@router.post("/proposals/{proposal_id}/cancel", response_model=ProposalView)
def cancel_proposal(
    workspace_id: UUID,
    proposal_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProposalView:
    _require_workspace(db, workspace_id, user.id)
    proposal = db.scalar(
        select(ActionProposal).where(
            ActionProposal.id == proposal_id,
            ActionProposal.workspace_id == workspace_id,
        )
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="Action proposal not found")
    if proposal.status != "draft":
        raise HTTPException(status_code=409, detail="Only an unapproved draft can be discarded")
    proposal.status = "cancelled"
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="action_proposal.cancelled",
            object_type="action_proposal",
            object_id=proposal.id,
            details_json={"payload_hash": proposal.payload_hash},
        )
    )
    db.commit()
    db.refresh(proposal)
    return _proposal_view(db, proposal)


@router.post("/proposals/{proposal_id}/approve", response_model=ProposalView)
def approve_proposal(
    workspace_id: UUID,
    proposal_id: UUID,
    payload: ProposalApproval,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProposalView:
    _require_workspace(db, workspace_id, user.id)
    proposal = db.scalar(
        select(ActionProposal).where(
            ActionProposal.id == proposal_id,
            ActionProposal.workspace_id == workspace_id,
        )
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="Action proposal not found")
    current_hash = _canonical_hash(proposal.target_json, proposal.payload_json)
    if proposal.status != "draft" or payload.payload_hash != proposal.payload_hash:
        raise HTTPException(status_code=409, detail="Approval must match the current draft payload")
    if current_hash != proposal.payload_hash:
        raise HTTPException(
            status_code=409, detail="Draft changed after it was prepared; review it again"
        )
    proposal.status = "approved"
    proposal.approved_by = user.id
    proposal.approved_at = datetime.now(UTC)
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="action_proposal.approved",
            object_type="action_proposal",
            object_id=proposal.id,
            details_json={"payload_hash": proposal.payload_hash},
        )
    )
    db.commit()
    db.refresh(proposal)
    return _proposal_view(db, proposal)


async def _get_tools(client: Client) -> dict[str, dict]:
    tools: dict[str, dict] = {}
    cursor: str | None = None
    for _ in range(10):
        page = await client.list_tools(cursor=cursor)
        for tool in page.tools:
            tools[tool.name] = tool.model_dump(mode="json", exclude_none=True, by_alias=True)
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    return tools


def _query_argument(tool: dict, query: str) -> dict | None:
    schema = tool.get("inputSchema") or {}
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    key = next(
        (candidate for candidate in ("query", "q", "search_query") if candidate in properties), None
    )
    required = schema.get("required", []) if isinstance(schema, dict) else []
    if not key or any(
        name != key and name not in {"perPage", "count", "limit"} for name in required
    ):
        return None
    args = {key: query}
    for limit_key in ("perPage", "count", "limit"):
        if limit_key in properties:
            args[limit_key] = 5
    if any(name not in args for name in required):
        return None
    return args


def _plain_result(result) -> list[dict]:
    structured = getattr(result, "structured_content", None)
    if structured is not None and hasattr(structured, "model_dump"):
        structured = structured.model_dump(mode="json", by_alias=True, exclude_none=True)
    values: list[dict] = []
    if structured is not None:
        values.append(structured if isinstance(structured, dict) else {"value": structured})
    for block in getattr(result, "content", []) or []:
        text_value = getattr(block, "text", None)
        if not isinstance(text_value, str):
            continue
        try:
            parsed = json.loads(text_value)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            values.append(parsed)
        elif isinstance(parsed, list):
            values.extend(item for item in parsed if isinstance(item, dict))
    return values


def _find_matching_issue(result, marker: str) -> tuple[str | None, str | None] | None:
    for item in normalize_mcp_result("github", "search_issues", result):
        if marker not in item.content_text:
            continue
        url = item.source_url
        number = None
        match = re.search(r"/issues/(\d+)(?:$|[?#])", url or "")
        if match:
            number = match.group(1)
        return number, url
    return None


def _issue_location(result, owner: str, repo: str) -> tuple[str | None, str | None]:
    def walk(value):
        if isinstance(value, dict):
            yield value
            for nested in value.values():
                yield from walk(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from walk(nested)

    for root in _plain_result(result):
        for item in walk(root):
            raw_url = item.get("html_url") or item.get("issue_url") or item.get("url")
            raw_number = item.get("number") or item.get("issue_number")
            number = str(raw_number) if isinstance(raw_number, (int, str)) else None
            if isinstance(raw_url, str):
                parsed = urlparse(raw_url)
                expected_prefix = f"/{owner}/{repo}/issues/"
                if (
                    parsed.scheme == "https"
                    and parsed.hostname == "github.com"
                    and parsed.path.startswith(expected_prefix)
                    and parsed.path[len(expected_prefix) :].split("/", 1)[0].isdigit()
                ):
                    number = parsed.path[len(expected_prefix) :].split("/", 1)[0]
                    return number, raw_url
            if number and number.isdigit():
                return number, f"https://github.com/{owner}/{repo}/issues/{number}"
    return None, None


def _execution_view(execution: ActionExecution) -> ActionExecutionView:
    return ActionExecutionView(
        status=execution.status,
        external_id=execution.external_id,
        external_url=execution.external_url,
        error_code=execution.error_code,
        completed_at=execution.completed_at,
    )


async def _search_action_marker(
    client: Client, tools: dict[str, dict], owner: str, repo: str, marker: str
):
    tool = tools.get("search_issues")
    annotations = (tool or {}).get("annotations") or {}
    if not tool or annotations.get("readOnlyHint") is not True:
        return None
    arguments = _query_argument(tool, f"repo:{owner}/{repo} in:body {marker}")
    if arguments is None:
        return None
    result = await client.call_tool("search_issues", arguments)
    if getattr(result, "is_error", False):
        raise RuntimeError("GitHub action reconciliation search failed")
    return _find_matching_issue(result, marker)


def _has_safe_reconciliation_tool(
    tools: dict[str, dict], owner: str, repo: str, marker: str
) -> bool:
    tool = tools.get("search_issues")
    annotations = (tool or {}).get("annotations") or {}
    return bool(
        tool
        and annotations.get("readOnlyHint") is True
        and _query_argument(tool, f"repo:{owner}/{repo} in:body {marker}") is not None
    )


@router.post("/proposals/{proposal_id}/execute", response_model=ProposalView)
async def execute_proposal(
    workspace_id: UUID,
    proposal_id: UUID,
    payload: ProposalApproval,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProposalView:
    _require_workspace(db, workspace_id, user.id)
    proposal = db.scalar(
        select(ActionProposal).where(
            ActionProposal.id == proposal_id,
            ActionProposal.workspace_id == workspace_id,
        )
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="Action proposal not found")
    if payload.payload_hash != proposal.payload_hash:
        raise HTTPException(status_code=409, detail="Execution must match the approved payload")
    if proposal.status == "executed":
        completed_execution = _execution_for(db, proposal.id)
        if completed_execution and completed_execution.status == "succeeded":
            return _proposal_view(db, proposal)
    if proposal.status != "approved":
        raise HTTPException(status_code=409, detail="Approve this exact proposal before execution")
    if _canonical_hash(proposal.target_json, proposal.payload_json) != proposal.payload_hash:
        raise HTTPException(
            status_code=409, detail="Approved payload has changed; create a new proposal"
        )

    integration = db.scalar(
        select(Integration).where(
            Integration.workspace_id == workspace_id,
            Integration.provider == "github",
            Integration.status == "connected",
        )
    )
    if integration is None or not integration.secret_ciphertext:
        raise HTTPException(status_code=409, detail="Reconnect the verified GitHub MCP server")
    try:
        access_token = decrypt_secret(integration.secret_ciphertext)
    except SecretStoreUnavailable as exc:
        raise HTTPException(
            status_code=409, detail="Stored GitHub credential cannot be used"
        ) from exc

    execution = _execution_for(db, proposal.id)
    existing_unknown = execution is not None and execution.status == "unknown"
    if execution and execution.status == "succeeded":
        return _proposal_view(db, proposal)
    if execution and execution.status == "pending":
        started_at = execution.started_at or datetime.now(UTC)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        age_seconds = (datetime.now(UTC) - started_at).total_seconds()
        if age_seconds < 120:
            return _proposal_view(db, proposal)
        existing_unknown = True
    if execution is None:
        execution = ActionExecution(
            proposal_id=proposal.id,
            idempotency_key=f"proposal:{proposal.id}:{proposal.payload_hash}",
            status="pending",
            started_at=datetime.now(UTC),
        )
        db.add(execution)
    else:
        execution.status = "pending"
        execution.error_code = None
        execution.started_at = datetime.now(UTC)
        execution.completed_at = None
    db.commit()
    db.refresh(execution)

    endpoint = integration.config_json.get("endpoint") or PROVIDERS["github"]["endpoint"]
    marker = proposal.target_json["action_reference"]
    owner = proposal.target_json["owner"]
    repo = proposal.target_json["repo"]
    headers = {"Authorization": f"Bearer {access_token}"}
    write_started = False
    try:
        async with httpx2.AsyncClient(
            headers=headers,
            timeout=httpx2.Timeout(20.0, read=45.0),
        ) as http_client:
            transport = streamable_http_client(endpoint, http_client=http_client)
            async with Client(transport, read_timeout_seconds=45.0) as client:
                tools = await _get_tools(client)
                if not _has_safe_reconciliation_tool(tools, owner, repo, marker):
                    execution.status = "failed"
                    execution.error_code = "reconciliation_unavailable"
                    execution.completed_at = datetime.now(UTC)
                    db.commit()
                    db.refresh(proposal)
                    return _proposal_view(db, proposal)
                existing = await _search_action_marker(client, tools, owner, repo, marker)
                if existing:
                    number, issue_url = existing
                    execution.status = "succeeded"
                    execution.external_id = number
                    execution.external_url = issue_url
                    execution.result_json = {"reconciled": True, "action_reference": marker}
                    execution.completed_at = datetime.now(UTC)
                    proposal.status = "executed"
                    db.add(
                        AuditEvent(
                            workspace_id=workspace_id,
                            actor_user_id=user.id,
                            event_type="action_execution.reconciled",
                            object_type="action_proposal",
                            object_id=proposal.id,
                            details_json={
                                "payload_hash": proposal.payload_hash,
                                "external_url": issue_url,
                            },
                        )
                    )
                    db.commit()
                    db.refresh(proposal)
                    return _proposal_view(db, proposal)

                if existing_unknown:
                    execution.status = "unknown"
                    execution.error_code = "reconciliation_not_found"
                    execution.completed_at = datetime.now(UTC)
                    db.commit()
                    db.refresh(proposal)
                    return _proposal_view(db, proposal)

                write_tool = tools.get("create_issue")
                schema = (write_tool or {}).get("inputSchema") or {}
                properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
                required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
                annotations = (write_tool or {}).get("annotations") or {}
                if (
                    not isinstance(properties, dict)
                    or not {"owner", "repo", "title"}.issubset(required)
                    or annotations.get("readOnlyHint") is not False
                ):
                    execution.status = "failed"
                    execution.error_code = "create_issue_unavailable"
                    execution.completed_at = datetime.now(UTC)
                    db.commit()
                    db.refresh(proposal)
                    return _proposal_view(db, proposal)
                args = {"owner": owner, "repo": repo, "title": proposal.payload_json["title"]}
                if "body" in properties:
                    args["body"] = proposal.payload_json["body"]
                if any(name not in args for name in required):
                    execution.status = "failed"
                    execution.error_code = "unsupported_create_issue_schema"
                    execution.completed_at = datetime.now(UTC)
                    db.commit()
                    db.refresh(proposal)
                    return _proposal_view(db, proposal)

                write_started = True
                result = await client.call_tool("create_issue", args)
                if getattr(result, "is_error", False):
                    execution.status = "failed"
                    execution.error_code = "github_rejected_action"
                    execution.completed_at = datetime.now(UTC)
                    db.add(
                        AuditEvent(
                            workspace_id=workspace_id,
                            actor_user_id=user.id,
                            event_type="action_execution.failed",
                            object_type="action_proposal",
                            object_id=proposal.id,
                            details_json={
                                "payload_hash": proposal.payload_hash,
                                "error_code": execution.error_code,
                            },
                        )
                    )
                else:
                    number, issue_url = _issue_location(result, owner, repo)
                    if number is None or issue_url is None:
                        execution.status = "unknown"
                        execution.error_code = "issue_result_unverified"
                        execution.completed_at = datetime.now(UTC)
                    else:
                        execution.status = "succeeded"
                        execution.external_id = number
                        execution.external_url = issue_url
                        execution.result_json = {"action_reference": marker}
                        execution.completed_at = datetime.now(UTC)
                        proposal.status = "executed"
                        db.add(
                            AuditEvent(
                                workspace_id=workspace_id,
                                actor_user_id=user.id,
                                event_type="action_execution.succeeded",
                                object_type="action_proposal",
                                object_id=proposal.id,
                                details_json={
                                    "payload_hash": proposal.payload_hash,
                                    "external_id": number,
                                    "external_url": issue_url,
                                },
                            )
                        )
                db.commit()
                db.refresh(proposal)
                return _proposal_view(db, proposal)
    except Exception as exc:
        logger.warning("GitHub action execution failed error_type=%s", type(exc).__name__)
        execution.status = "unknown" if write_started else "failed"
        execution.error_code = (
            "provider_outcome_unknown" if write_started else "provider_unavailable_before_write"
        )
        execution.completed_at = datetime.now(UTC)
        db.add(
            AuditEvent(
                workspace_id=workspace_id,
                actor_user_id=user.id,
                event_type="action_execution.unknown"
                if write_started
                else "action_execution.failed",
                object_type="action_proposal",
                object_id=proposal.id,
                details_json={"payload_hash": proposal.payload_hash},
            )
        )
        db.commit()
        db.refresh(proposal)
        return _proposal_view(db, proposal)
