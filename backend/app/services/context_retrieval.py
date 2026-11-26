import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.api.v1.integrations import PROVIDERS
from app.models.entities import Integration

QUERY_FIELDS = ("query", "search_query", "searchQuery", "q", "search")
RESULT_LIMIT_FIELDS = ("count", "limit", "perPage", "page_size", "pageSize", "max_results")
BLOCKED_TOOL_WORDS = {
    "add",
    "approve",
    "complete",
    "create",
    "delete",
    "edit",
    "post",
    "react",
    "remove",
    "schedule",
    "send",
    "update",
    "upload",
    "write",
}
GITHUB_SEARCH_TOOLS = {"search_issues", "search_code", "search_pull_requests"}
MAX_SEARCH_TOOLS_PER_PROVIDER = 2
MAX_CONTEXT_ITEMS = 20
MAX_ITEM_CHARACTERS = 5000
MAX_TOTAL_CHARACTERS = 50000
STOP_WORDS = {
    "about",
    "after",
    "before",
    "from",
    "have",
    "into",
    "that",
    "them",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
    "your",
}
GITHUB_REPOSITORIES_URL = "https://api.github.com/user/repos"
GITHUB_API_VERSION = "2022-11-28"


@dataclass(frozen=True)
class RetrievedContext:
    provider: str
    tool_name: str
    external_id: str
    source_url: str | None
    title: str
    content_text: str
    author: str | None
    occurred_at: datetime | None
    metadata: dict


def _field(record: dict, *keys: str):
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def _nested_text(value, depth: int = 0) -> str:
    if depth > 3 or value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(text for item in value if (text := _nested_text(item, depth + 1)))
    if isinstance(value, dict):
        for key in ("text", "plain_text", "content", "value", "markdown"):
            text = _nested_text(value.get(key), depth + 1)
            if text:
                return text
    return ""


def _has_record_content(record: dict) -> bool:
    return any(
        _nested_text(record.get(key))
        for key in (
            "title",
            "text",
            "body",
            "content",
            "description",
            "summary",
            "snippet",
            "message",
        )
    )


def _record_candidates(value) -> list[dict]:
    if isinstance(value, list):
        rows: list[dict] = []
        for item in value:
            rows.extend(_record_candidates(item))
        return rows
    if isinstance(value, dict):
        if _has_record_content(value):
            return [value]
        rows = []
        for item in value.values():
            rows.extend(_record_candidates(item))
        return rows
    return []


def _safe_source_url(value, provider: str) -> str | None:
    if not isinstance(value, str) or len(value) > 2000:
        return None
    value = value.strip().strip("\"'<>.,;)]}")
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return None
    if provider == "github":
        allowed = (
            host == "github.com"
            or host.endswith(".github.com")
            or host == "raw.githubusercontent.com"
        )
    else:
        allowed = host == "slack.com" or host.endswith(".slack.com")
    return value if allowed else None


def _date_value(record: dict) -> datetime | None:
    value = _field(record, "occurred_at", "created_at", "updated_at", "timestamp", "ts", "date")
    try:
        if isinstance(value, (float, int)):
            return datetime.fromtimestamp(value, UTC)
        if isinstance(value, str):
            if re.fullmatch(r"\d+(?:\.\d+)?", value):
                return datetime.fromtimestamp(float(value), UTC)
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    except (OverflowError, OSError, ValueError):
        return None
    return None


def _author_value(record: dict) -> str | None:
    value = _field(record, "author", "username", "user_name", "display_name")
    if value is None and isinstance(record.get("user"), dict):
        value = _field(record["user"], "login", "name", "username", "real_name")
    if isinstance(value, dict):
        value = _field(value, "login", "name", "username", "real_name")
    return str(value)[:255] if value is not None else None


def normalize_mcp_result(provider: str, tool_name: str, result) -> list[RetrievedContext]:
    structured = getattr(result, "structured_content", None)
    if structured is not None and hasattr(structured, "model_dump"):
        structured = structured.model_dump(mode="json", by_alias=True, exclude_none=True)
    candidate_records = _record_candidates(structured)
    fallback_text: list[str] = []
    for block in getattr(result, "content", []) or []:
        block_text = getattr(block, "text", None)
        if isinstance(block_text, str) and block_text.strip():
            fallback_text.append(block_text.strip())

    if not candidate_records:
        for block_text in fallback_text:
            try:
                parsed_content = json.loads(block_text)
            except (json.JSONDecodeError, TypeError):
                continue
            candidate_records.extend(_record_candidates(parsed_content))

    if not candidate_records and fallback_text:
        candidate_records = [{"text": text} for text in fallback_text]
    if not candidate_records:
        return []

    normalized: list[RetrievedContext] = []
    used = 0
    for index, record in enumerate(candidate_records[:MAX_CONTEXT_ITEMS]):
        pieces: list[str] = []
        for key in (
            "title",
            "text",
            "body",
            "content",
            "description",
            "summary",
            "snippet",
            "message",
        ):
            text = _nested_text(record.get(key))
            if text and text not in pieces:
                pieces.append(text)
        content = "\n\n".join(pieces).strip()
        if not content:
            continue
        remaining = MAX_TOTAL_CHARACTERS - used
        if remaining <= 0:
            break
        content = content[: min(MAX_ITEM_CHARACTERS, remaining)]
        used += len(content)

        raw_title = _nested_text(_field(record, "title", "name", "subject"))
        title = (raw_title or content.splitlines()[0])[:500]
        raw_url = _field(record, "source_url", "html_url", "permalink", "url", "link")
        source_url = _safe_source_url(raw_url, provider)
        if source_url is None:
            for candidate_url in re.findall(r"https://[^\s<>\]\[)]+", content):
                source_url = _safe_source_url(candidate_url.rstrip(".,;"), provider)
                if source_url:
                    break
        source_identity = source_url or _field(record, "id", "ts", "timestamp", "number", "key")
        if source_identity is None:
            canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
            source_identity = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        external_id = f"{tool_name}:{str(source_identity)}"[:255]
        normalized.append(
            RetrievedContext(
                provider=provider,
                tool_name=tool_name,
                external_id=external_id,
                source_url=source_url,
                title=title,
                content_text=content,
                author=_author_value(record),
                occurred_at=_date_value(record),
                metadata={"provider": provider, "tool_name": tool_name, "result_index": index},
            )
        )
    return normalized


def _is_safe_search_tool(provider: str, tool: dict) -> bool:
    name = tool.get("name")
    if not isinstance(name, str):
        return False
    lowered = name.lower()
    if any(word in lowered.split("_") for word in BLOCKED_TOOL_WORDS):
        return False
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    query_key = next((key for key in QUERY_FIELDS if key in props), None)
    if query_key is None:
        return False
    annotation = tool.get("annotations") or {}
    read_only = annotation.get("readOnlyHint", annotation.get("read_only_hint")) is True
    title_description = " ".join(str(tool.get(key, "")) for key in ("title", "description")).lower()
    text = f"{lowered} {title_description}"
    if provider == "github":
        return read_only and name in GITHUB_SEARCH_TOOLS
    return read_only and any(word in text for word in ("search", "find"))


def _tool_arguments(tool: dict, prompt: str) -> dict | None:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    query_key = next((key for key in QUERY_FIELDS if key in props), None)
    if query_key is None:
        return None
    args = {query_key: prompt[:256]}
    for key in RESULT_LIMIT_FIELDS:
        if key in props:
            args[key] = 5
    required = schema.get("required", []) if isinstance(schema, dict) else []
    if any(key not in args for key in required):
        return None
    return args


def relevance_score(prompt: str, content: str) -> float:
    def tokens(value: str) -> set[str]:
        return {
            token.lower()
            for token in re.findall(r"[\w-]{3,}", value)
            if token.lower() not in STOP_WORDS
        }

    query_terms = tokens(prompt)
    if not query_terms:
        return 0.0
    content_terms = tokens(content)
    return round(len(query_terms & content_terms) / len(query_terms), 4)


def github_repository_intent(prompt: str) -> str | None:
    value = prompt.casefold()
    if not re.search(r"\b(repos?|repositories)\b", value):
        return None
    if re.search(r"\b(how many|count|number of|total)\b", value):
        return "count"
    if re.search(r"\b(list|show|which)\b", value) or re.search(
        r"\bwhat\s+(?:repos?|repositories)\b", value
    ):
        return "list"
    return None


async def retrieve_github_repository_summary(
    access_token: str, prompt: str
) -> list[RetrievedContext]:
    intent = github_repository_intent(prompt)
    if intent is None:
        return []
    page_size = 1 if intent == "count" else 20
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    async with httpx2.AsyncClient(
        headers=headers,
        timeout=httpx2.Timeout(20.0, read=45.0),
        follow_redirects=False,
    ) as client:
        response = await client.get(
            GITHUB_REPOSITORIES_URL,
            params={
                "visibility": "all",
                "affiliation": "owner,collaborator,organization_member",
                "sort": "updated",
                "per_page": page_size,
                "page": 1,
            },
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or any(not isinstance(repo, dict) for repo in payload):
        raise RuntimeError("GitHub returned an invalid repository list")
    nodes = payload
    link_header = response.headers.get("link", "")
    last_page = None
    for link in link_header.split(","):
        if 'rel="last"' not in link:
            continue
        match = re.search(r"[?&]page=(\d+)", link)
        if match:
            last_page = int(match.group(1))
            break
    count = (last_page if last_page is not None else len(nodes)) if intent == "count" else None

    lines = []
    if intent == "count":
        lines = [
            f"The connected GitHub token can access {count} repositories.",
            "This count comes from GitHub's authenticated user repository pagination.",
        ]
    if intent == "list":
        for repository in nodes:
            name = repository.get("full_name")
            if not isinstance(name, str):
                continue
            privacy = "private" if repository.get("private") else "public"
            language = repository.get("language")
            description = str(repository.get("description") or "").strip()
            url = repository.get("html_url")
            lines.append(
                " — ".join(
                    part
                    for part in (
                        f"{name} ({privacy})",
                        language,
                        description[:400] if description else None,
                        url if isinstance(url, str) else None,
                    )
                    if part
                )
            )
        if not lines:
            lines.append("GitHub returned no repositories for the connected token.")
        elif response.headers.get("link") and 'rel="next"' in link_header:
            lines.append(f"Showing the {len(nodes)} most recently updated repositories.")

    source_url = "https://github.com/settings/repositories"
    return [
        RetrievedContext(
            provider="github",
            tool_name="github_rest_repository_summary",
            external_id="github:repository_summary",
            source_url=source_url,
            title="Repositories accessible to the connected GitHub token",
            content_text="\n".join(lines),
            author=None,
            occurred_at=datetime.now(UTC),
            metadata={
                "provider": "github",
                "tool_name": "github_rest_repository_summary",
                "repository_count": count,
                "query_kind": intent,
                "returned_repository_count": len(nodes),
            },
        )
    ]


async def retrieve_from_integration(
    integration: Integration,
    access_token: str,
    prompt: str,
) -> list[RetrievedContext]:
    endpoint = integration.config_json.get("endpoint") or PROVIDERS.get(
        integration.provider, {}
    ).get("endpoint")
    if not endpoint:
        return []
    if integration.provider == "github" and github_repository_intent(prompt):
        try:
            repository_results = await retrieve_github_repository_summary(access_token, prompt)
            if repository_results:
                return repository_results
        except Exception:
            # MCP search remains a fallback when the account token lacks GraphQL access.
            pass
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx2.AsyncClient(
        headers=headers,
        timeout=httpx2.Timeout(20.0, read=45.0),
    ) as http_client:
        transport = streamable_http_client(endpoint, http_client=http_client)
        async with Client(transport, read_timeout_seconds=45.0) as client:
            current_tools: list[dict] = []
            cursor: str | None = None
            for _ in range(10):
                page = await client.list_tools(cursor=cursor)
                current_tools.extend(
                    tool.model_dump(mode="json", exclude_none=True, by_alias=True)
                    for tool in page.tools
                )
                if not page.next_cursor:
                    break
                cursor = page.next_cursor

            eligible = [
                tool for tool in current_tools if _is_safe_search_tool(integration.provider, tool)
            ]
            eligible = eligible[:MAX_SEARCH_TOOLS_PER_PROVIDER]
            results: list[RetrievedContext] = []
            total_characters = 0
            for tool in eligible:
                arguments = _tool_arguments(tool, prompt)
                if arguments is None:
                    continue
                result = await client.call_tool(tool["name"], arguments)
                if getattr(result, "is_error", False):
                    continue
                normalized = normalize_mcp_result(integration.provider, tool["name"], result)
                for item in normalized:
                    if total_characters + len(item.content_text) > MAX_TOTAL_CHARACTERS:
                        return results
                    results.append(item)
                    total_characters += len(item.content_text)
                    if len(results) >= MAX_CONTEXT_ITEMS:
                        return results
            return results
