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

from app.api.v1 import actions, integrations
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app


async def _exercise_api(action: Callable[[httpx2.AsyncClient], Awaitable[None]]) -> None:
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


def test_github_issue_requires_exact_approval_and_reconciles_idempotently(monkeypatch) -> None:
    server = MCPServer("SAHAKARYA reviewed action test", version="1.0")
    issues: list[dict] = []
    calls = {"search": 0, "write": 0}

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def search_issues(query: str, perPage: int = 5) -> dict:
        """Search GitHub issues by query."""
        calls["search"] += 1
        if "sahakarya-action-" in query:
            return {"issues": issues[:perPage]}
        return {
            "issues": [
                {
                    "id": 47,
                    "title": "Stage the API migration",
                    "body": "Use a compatibility window for the v2 API rollout.",
                    "html_url": "https://github.com/example/project/issues/47",
                    "user": {"login": "vinay"},
                }
            ]
        }

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def create_issue(owner: str, repo: str, title: str, body: str = "") -> dict:
        """Create a GitHub issue."""
        calls["write"] += 1
        number = 80 + calls["write"]
        created = {
            "number": number,
            "title": title,
            "body": body,
            "html_url": f"https://github.com/{owner}/{repo}/issues/{number}",
        }
        issues.append(created)
        return created

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
    monkeypatch.setattr(actions.httpx2, "AsyncClient", local_client_factory)
    monkeypatch.setitem(integrations.PROVIDERS["github"], "endpoint", endpoint)
    monkeypatch.setattr(
        settings, "integration_encryption_key", Fernet.generate_key().decode("ascii")
    )

    async def scenario(client: httpx2.AsyncClient) -> None:
        async with server_app.router.lifespan_context(server_app):
            registration = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "action-user@example.com",
                    "display_name": "Action User",
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

            context_response = await client.post(
                f"/api/v1/workspaces/{workspace_id}/requests",
                json={"prompt": "How should we handle the API migration?"},
            )
            assert context_response.status_code == 201
            context = context_response.json()
            assert len(context["sources"]) == 1

            proposal_response = await client.post(
                f"/api/v1/workspaces/{workspace_id}/requests/{context['request_id']}/proposals",
                json={
                    "repository": "example/project",
                    "title": "Plan the API migration",
                    "description": "Document the compatibility window and rollout stages.",
                    "source_context_ids": [context["sources"][0]["id"]],
                },
            )
            assert proposal_response.status_code == 201
            proposal = proposal_response.json()
            assert proposal["status"] == "draft"
            assert "Context sources:" in proposal["payload"]["body"]
            assert context["sources"][0]["source_url"] in proposal["payload"]["body"]
            assert "sahakarya-action-" in proposal["payload"]["body"]
            assert calls["write"] == 0

            history = await client.get(
                f"/api/v1/workspaces/{workspace_id}/requests/{context['request_id']}/proposals"
            )
            assert history.status_code == 200
            assert history.json()[0]["id"] == proposal["id"]

            original_hash = proposal["payload_hash"]
            revised = await client.patch(
                f"/api/v1/workspaces/{workspace_id}/proposals/{proposal['id']}",
                json={
                    "repository": "example/project",
                    "title": "Plan the staged API migration",
                    "description": "Revised follow-up: document the compatibility window.",
                },
            )
            assert revised.status_code == 200
            proposal = revised.json()
            assert proposal["payload_hash"] != original_hash
            assert proposal["editable_description"].startswith("Revised follow-up:")
            assert "Action reference:" in proposal["payload"]["body"]

            hash_mismatch = await client.post(
                f"/api/v1/workspaces/{workspace_id}/proposals/{proposal['id']}/approve",
                json={"payload_hash": original_hash},
            )
            assert hash_mismatch.status_code == 409
            assert calls["write"] == 0

            approval = await client.post(
                f"/api/v1/workspaces/{workspace_id}/proposals/{proposal['id']}/approve",
                json={"payload_hash": proposal["payload_hash"]},
            )
            assert approval.status_code == 200
            assert approval.json()["status"] == "approved"

            execution = await client.post(
                f"/api/v1/workspaces/{workspace_id}/proposals/{proposal['id']}/execute",
                json={"payload_hash": proposal["payload_hash"]},
            )
            assert execution.status_code == 200
            result = execution.json()
            assert result["status"] == "executed"
            assert result["execution"]["status"] == "succeeded"
            assert (
                result["execution"]["external_url"]
                == "https://github.com/example/project/issues/81"
            )
            assert calls["write"] == 1
            assert "Context sources:" in issues[0]["body"]

            duplicate_execution = await client.post(
                f"/api/v1/workspaces/{workspace_id}/proposals/{proposal['id']}/execute",
                json={"payload_hash": proposal["payload_hash"]},
            )
            assert duplicate_execution.status_code == 200
            assert (
                duplicate_execution.json()["execution"]["external_url"]
                == result["execution"]["external_url"]
            )
            assert calls["write"] == 1

    asyncio.run(_exercise_api(scenario))
