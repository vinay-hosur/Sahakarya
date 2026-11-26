import asyncio
from collections.abc import Awaitable, Callable

import httpx2
from cryptography.fernet import Fernet
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1 import integrations, requests
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app


async def _exercise_api(
    action: Callable[[httpx2.AsyncClient], Awaitable[None]],
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        with test_sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await action(client)
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_context_request_uses_verified_read_tool_and_persists_cited_evidence(monkeypatch) -> None:
    server = MCPServer("SAHAKARYA context test", version="1.0")
    calls = {"search": 0, "write": 0}

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def search_issues(query: str, perPage: int = 5) -> dict:
        """Search GitHub issues using natural-language terms."""
        calls["search"] += 1
        assert "API migration" in query
        return {
            "issues": [
                {
                    "id": 472,
                    "title": "Decide the API migration plan",
                    "body": "The team will use a staged API migration with a compatibility window.",
                    "html_url": "https://github.com/example/project/issues/472",
                    "user": {"login": "vinay"},
                    "updated_at": "2026-11-25T10:30:00Z",
                }
            ],
            "per_page": perPage,
        }

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def create_issue(title: str) -> str:
        """Create a GitHub issue."""
        calls["write"] += 1
        return title

    server_app = server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(allowed_hosts=["127.0.0.1"]),
    )
    real_client_factory = httpx2.AsyncClient
    endpoint = "http://127.0.0.1/mcp"

    def local_client_factory(**kwargs):
        if "transport" not in kwargs:
            kwargs["transport"] = httpx2.ASGITransport(app=server_app)
            kwargs["base_url"] = "http://127.0.0.1"
        return real_client_factory(**kwargs)

    monkeypatch.setattr(integrations.httpx2, "AsyncClient", local_client_factory)
    monkeypatch.setitem(integrations.PROVIDERS["github"], "endpoint", endpoint)
    monkeypatch.setattr(
        settings, "integration_encryption_key", Fernet.generate_key().decode("ascii")
    )

    async def fake_reasoning(_request, sources, *, provider=None, **_kwargs):
        return {
            "provider": provider or "openai",
            "model": "offline-test-model",
            "answer": "The team chose a staged API migration.",
            "citation_ids": [str(sources[0].id)],
            "needs_more_context": False,
            "suggested_action": {
                "title": "Document the migration plan",
                "description": "Capture the compatibility window and rollout stages.",
            },
        }

    monkeypatch.setattr(requests, "reason_over_context", fake_reasoning)

    async def scenario(client: httpx2.AsyncClient) -> None:
        async with server_app.router.lifespan_context(server_app):
            registration = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "context-user@example.com",
                    "display_name": "Context User",
                    "password": "correct-horse-battery-staple",
                },
            )
            assert registration.status_code == 201
            workspace_id = registration.json()["workspaces"][0]["id"]

            connection = await client.post(
                f"/api/v1/workspaces/{workspace_id}/integrations/github/connect",
                json={"access_token": "github-local-mcp-token-1234"},
            )
            assert connection.status_code == 200
            assert connection.json()["status"] == "connected"
            assert {tool["name"] for tool in connection.json()["capabilities"]} == {
                "search_issues",
                "create_issue",
            }

            request_response = await client.post(
                f"/api/v1/workspaces/{workspace_id}/requests",
                json={"prompt": "What is the API migration decision?"},
            )
            assert request_response.status_code == 201
            result = request_response.json()
            assert result["status"] == "completed"
            assert len(result["sources"]) == 1
            source = result["sources"][0]
            assert source["provider"] == "github"
            assert source["tool_name"] == "search_issues"
            assert source["source_url"] == "https://github.com/example/project/issues/472"
            assert "compatibility window" in source["content_text"]
            assert source["author"] == "vinay"
            assert result["reasoning"]["answer"] == "The team chose a staged API migration."
            assert result["reasoning"]["citation_ids"] == [source["id"]]
            assert calls == {"search": 1, "write": 0}

            stored = await client.get(
                f"/api/v1/workspaces/{workspace_id}/requests/{result['request_id']}"
            )
            assert stored.status_code == 200
            assert stored.json()["sources"][0]["id"] == source["id"]
            assert (
                stored.json()["reasoning"]["suggested_action"]["title"]
                == "Document the migration plan"
            )

    asyncio.run(_exercise_api(scenario))


def test_no_connected_sources_returns_actionable_status() -> None:
    async def scenario(client: httpx2.AsyncClient) -> None:
        registration = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "empty-context@example.com",
                "display_name": "Empty Context",
                "password": "correct-horse-battery-staple",
            },
        )
        workspace_id = registration.json()["workspaces"][0]["id"]
        result = await client.post(
            f"/api/v1/workspaces/{workspace_id}/requests",
            json={"prompt": "What decisions were made?"},
        )
        assert result.status_code == 201
        assert result.json()["status"] == "needs_sources"
        assert result.json()["sources"] == []
        assert "Connect a verified MCP source" in result.json()["detail"]

    asyncio.run(_exercise_api(scenario))
