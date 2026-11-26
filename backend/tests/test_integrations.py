import asyncio
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs, urlparse

import httpx2
from cryptography.fernet import Fernet
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1 import integrations
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import AuditEvent, Integration, IntegrationOAuthState


async def _exercise_api(
    action: Callable[[object], Awaitable[None]],
    verify_db: Callable[[object], None] | None = None,
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
        if verify_db:
            verify_db(test_sessions)
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_mcp_streamable_http_verifies_protocol_and_discovers_tools(monkeypatch) -> None:
    server = MCPServer("SAHAKARYA integration test", version="1.2.3")

    @server.tool()
    async def search_project(query: str) -> str:
        """Find project context for a search phrase."""
        return f"Found context for {query}"

    server_app = server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(allowed_hosts=["127.0.0.1"]),
    )
    real_client_factory = httpx2.AsyncClient

    def local_client_factory(*, headers, timeout):
        return real_client_factory(
            headers=headers,
            timeout=timeout,
            transport=httpx2.ASGITransport(app=server_app),
            base_url="http://127.0.0.1",
        )

    monkeypatch.setattr(integrations.httpx2, "AsyncClient", local_client_factory)

    async def run_discovery():
        async with server_app.router.lifespan_context(server_app):
            return await integrations.discover_mcp("http://127.0.0.1/mcp", "test-access-token")

    protocol, server_info, tools = asyncio.run(run_discovery())

    assert protocol == "2026-07-28"
    assert server_info is not None
    assert server_info["name"] == "SAHAKARYA integration test"
    assert [tool["name"] for tool in tools] == ["search_project"]
    assert tools[0]["inputSchema"]["required"] == ["query"]


def test_connector_token_is_encrypted_and_status_follows_capability_check(monkeypatch) -> None:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(settings, "integration_encryption_key", key)

    async def verified(endpoint: str, token: str):
        assert endpoint == integrations.PROVIDERS["github"]["endpoint"]
        assert token == "github-test-token-123456"
        return (
            "2026-07-28",
            {"name": "GitHub MCP"},
            [{"name": "get_file_contents", "inputSchema": {"type": "object"}}],
        )

    monkeypatch.setattr(integrations, "discover_mcp", verified)

    async def scenario(client) -> None:
        registration = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "connector@example.com",
                "display_name": "Connector Owner",
                "password": "correct-horse-battery-staple",
            },
        )
        workspace_id = registration.json()["workspaces"][0]["id"]
        connected = await client.post(
            f"/api/v1/workspaces/{workspace_id}/integrations/github/connect",
            json={"access_token": "github-test-token-123456"},
        )
        assert connected.status_code == 200
        assert connected.json()["status"] == "connected"
        assert connected.json()["capabilities"][0]["name"] == "get_file_contents"
        assert "github-test-token-123456" not in connected.text
        assert connected.json()["credential_configured"] is True

        entries = await client.get(f"/api/v1/workspaces/{workspace_id}/integrations")
        assert entries.status_code == 200
        assert len(entries.json()) == 1
        assert entries.json()[0]["status"] == "connected"

    asyncio.run(_exercise_api(scenario))


def test_connector_failure_never_claims_connected(monkeypatch) -> None:
    monkeypatch.setattr(
        settings, "integration_encryption_key", Fernet.generate_key().decode("ascii")
    )

    async def rejected(endpoint: str, token: str):
        raise PermissionError("credential must never be shown")

    monkeypatch.setattr(integrations, "discover_mcp", rejected)

    async def scenario(client) -> None:
        registration = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "failed-connector@example.com",
                "display_name": "Failed Connector",
                "password": "correct-horse-battery-staple",
            },
        )
        workspace_id = registration.json()["workspaces"][0]["id"]
        rejected_response = await client.post(
            f"/api/v1/workspaces/{workspace_id}/integrations/github/connect",
            json={"access_token": "github-test-token-123456"},
        )
        assert rejected_response.status_code == 502
        assert "credential must never be shown" not in rejected_response.text

        listed = await client.get(f"/api/v1/workspaces/{workspace_id}/integrations")
        assert listed.json()[0]["status"] == "error"
        assert listed.json()[0]["credential_configured"] is True

    asyncio.run(_exercise_api(scenario))


def test_slack_raw_token_is_rejected_in_favor_of_registered_app_oauth() -> None:
    async def scenario(client) -> None:
        registration = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "slack-unavailable@example.com",
                "display_name": "Slack Unavailable",
                "password": "correct-horse-battery-staple",
            },
        )
        workspace_id = registration.json()["workspaces"][0]["id"]
        rejected = await client.post(
            f"/api/v1/workspaces/{workspace_id}/integrations/slack/connect",
            json={"access_token": "slack-test-token-123456"},
        )
        assert rejected.status_code == 409
        assert "registered-app OAuth flow" in rejected.json()["detail"]

        listed = await client.get(f"/api/v1/workspaces/{workspace_id}/integrations")
        assert listed.status_code == 200
        assert listed.json() == []

    asyncio.run(_exercise_api(scenario))


def _configure_slack_oauth(monkeypatch) -> None:
    monkeypatch.setattr(settings, "slack_client_id", "test-client-id")
    monkeypatch.setattr(settings, "slack_client_secret", SecretStr("test-client-secret"))
    monkeypatch.setattr(
        settings,
        "slack_redirect_uri",
        "https://app.example.test/api/v1/integrations/slack/oauth/callback",
    )
    monkeypatch.setattr(settings, "slack_frontend_url", "https://app.example.test")
    monkeypatch.setattr(
        settings, "integration_encryption_key", Fernet.generate_key().decode("ascii")
    )


def test_slack_oauth_start_stores_only_a_hash_of_state(monkeypatch) -> None:
    _configure_slack_oauth(monkeypatch)
    captured: dict[str, str] = {}

    async def scenario(client) -> None:
        registration = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "slack-oauth-start@example.com",
                "display_name": "Slack OAuth Owner",
                "password": "correct-horse-battery-staple",
            },
        )
        workspace_id = registration.json()["workspaces"][0]["id"]
        started = await client.post(
            f"/api/v1/workspaces/{workspace_id}/integrations/slack/oauth/start",
            json={},
        )
        assert started.status_code == 200
        authorization_url = started.json()["authorization_url"]
        query = parse_qs(urlparse(authorization_url).query)
        assert urlparse(authorization_url).scheme == "https"
        assert urlparse(authorization_url).netloc == "slack.com"
        assert query["client_id"] == ["test-client-id"]
        assert query["redirect_uri"] == [settings.slack_redirect_uri]
        assert query["scope"]
        captured["state"] = query["state"][0]

    def verify_db(test_sessions) -> None:
        with test_sessions() as db:
            row = db.scalar(select(IntegrationOAuthState))
            assert row is not None
            assert row.state_hash != captured["state"]
            assert len(row.state_hash) == 64

    asyncio.run(_exercise_api(scenario, verify_db))


def test_slack_oauth_callback_verifies_mcp_encrypts_token_and_allows_disconnect(
    monkeypatch,
) -> None:
    _configure_slack_oauth(monkeypatch)
    token = "xoxp-test-only-token"

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "ok": True,
                "access_token": token,
                "team": {"id": "T123", "name": "Test Team"},
                "authed_user": {"id": "U123"},
                "scope": "search:read.public,channels:history",
            }

    class FakeRevokeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, url: str, **kwargs):
            if url.endswith("oauth.v2.user.access"):
                assert kwargs["data"]["code"] == "test-authorization-code"
                assert kwargs["auth"] == ("test-client-id", "test-client-secret")
                return FakeResponse()
            assert url.endswith("auth.revoke")
            assert kwargs["headers"]["Authorization"] == f"Bearer {token}"
            return FakeRevokeResponse()

    async def discovered(endpoint: str, access_token: str):
        assert endpoint == integrations.PROVIDERS["slack"]["endpoint"]
        assert access_token == token
        return "2026-07-28", {"name": "Slack MCP"}, [{"name": "search_messages"}]

    monkeypatch.setattr(integrations, "discover_mcp", discovered)
    captured: dict[str, str] = {}

    async def scenario(client) -> None:
        monkeypatch.setattr(integrations.httpx2, "AsyncClient", FakeAsyncClient)
        registration = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "slack-oauth-callback@example.com",
                "display_name": "Slack OAuth Owner",
                "password": "correct-horse-battery-staple",
            },
        )
        workspace_id = registration.json()["workspaces"][0]["id"]
        started = await client.post(
            f"/api/v1/workspaces/{workspace_id}/integrations/slack/oauth/start",
            json={},
        )
        state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
        callback = await client.get(
            "/api/v1/integrations/slack/oauth/callback",
            params={"code": "test-authorization-code", "state": state},
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert "slack_oauth=connected" in callback.headers["location"]
        connected = await client.get(f"/api/v1/workspaces/{workspace_id}/integrations")
        assert connected.status_code == 200
        assert len(connected.json()) == 1
        assert connected.json()[0]["status"] == "connected"
        assert connected.json()[0]["credential_configured"] is True
        assert token not in connected.text

        replay = await client.get(
            "/api/v1/integrations/slack/oauth/callback",
            params={"code": "test-authorization-code", "state": state},
            follow_redirects=False,
        )
        assert "slack_oauth=invalid_state" in replay.headers["location"]

        removed = await client.delete(f"/api/v1/workspaces/{workspace_id}/integrations/slack")
        assert removed.status_code == 204
        after_disconnect = await client.get(f"/api/v1/workspaces/{workspace_id}/integrations")
        assert after_disconnect.json() == []
        captured["workspace_id"] = workspace_id

    def verify_db(test_sessions) -> None:
        with test_sessions() as db:
            integration = db.scalar(select(Integration))
            assert integration is None
            events = db.scalars(select(AuditEvent)).all()
            assert {event.event_type for event in events} >= {
                "integration.connection_checked",
                "integration.disconnected",
            }
            assert db.scalar(select(IntegrationOAuthState)) is None

    asyncio.run(_exercise_api(scenario, verify_db))
