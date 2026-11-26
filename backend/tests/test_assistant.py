import asyncio
from collections.abc import Awaitable, Callable

import httpx
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1 import assistant as assistant_api
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import AssistantMemory, AssistantMessage, AuditEvent


async def _exercise_api(
    action: Callable[[httpx.AsyncClient, sessionmaker], Awaitable[None]],
) -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        with test_sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await action(client, test_sessions)
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


async def _register(client: httpx.AsyncClient, email: str, display_name: str) -> dict:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "display_name": display_name,
            "password": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_workspace_assistant_persists_grounded_messages_and_manageable_memory(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("test-provider-key"))
    monkeypatch.setattr(settings, "openai_model", "test-model")
    captured: dict = {}

    async def fake_reasoning(
        request, sources, *, provider=None, history=None, allow_empty_context=False
    ):
        captured.update(
            {"prompt": request.prompt, "sources": sources, "provider": provider, "history": history}
        )
        return {
            "provider": provider,
            "model": "test-model",
            "answer": (
                "The onboarding notes describe token refresh and the team’s compatibility rule."
            ),
            "citation_ids": [str(sources[0].id)] if sources else [],
            "needs_more_context": not sources,
            "usage": {"input_tokens": 42, "output_tokens": 18, "total_tokens": 60},
            "suggested_action": None,
        }

    monkeypatch.setattr(assistant_api, "reason_over_context", fake_reasoning)

    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        owner = await _register(client, "assistant-owner@example.com", "Assistant Owner")
        workspace_id = owner["workspaces"][0]["id"]
        project = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects",
            json={"name": "Identity", "description": "Secure sign-in and sessions."},
        )
        assert project.status_code == 201, project.text
        project_id = project.json()["id"]
        document = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            json={
                "title": "Onboarding notes",
                "content": (
                    "Refresh tokens rotate after sign-in. Keep the old API behavior supported "
                    "for two releases."
                ),
                "project_id": project_id,
            },
        )
        assert document.status_code == 201, document.text

        base = f"/api/v1/workspaces/{workspace_id}/assistant"
        saved = await client.post(
            f"{base}/memories",
            json={
                "content": "Keep API changes backwards compatible for two releases.",
                "project_id": project_id,
            },
        )
        assert saved.status_code == 201, saved.text
        memory_id = saved.json()["id"]

        sent = await client.post(
            f"{base}/chat",
            json={
                "prompt": "Write an onboarding brief for the identity project.",
                "project_id": project_id,
                "provider": "openai",
            },
        )
        assert sent.status_code == 201, sent.text
        assert sent.json()["provider"] == "openai"
        assert sent.json()["usage"]["total_tokens"] == 60
        assert sent.json()["citations"][0]["title"] == "Onboarding notes"
        assert captured["provider"] == "openai"
        assert captured["history"] == []
        assert any(source.provider == "workspace_memory" for source in captured["sources"])

        history = await client.get(f"{base}/messages")
        assert history.status_code == 200
        assert [message["role"] for message in history.json()] == ["user", "assistant"]
        assert history.json()[0]["author_display_name"] == "Assistant Owner"
        assert history.json()[1]["citations"][0]["id"] == document.json()["id"]
        assert history.json()[1]["usage"]["input_tokens"] == 42
        memories = await client.get(f"{base}/memories", params={"project_id": project_id})
        assert [memory["id"] for memory in memories.json()] == [memory_id]

        invite = await client.post(
            f"/api/v1/workspaces/{workspace_id}/invitations",
            json={"email": "assistant-viewer@example.com", "role": "viewer"},
        )
        assert invite.status_code == 201
        await client.post("/api/v1/auth/logout")
        viewer = await _register(client, "assistant-viewer@example.com", "Assistant Viewer")
        accepted = await client.post(
            "/api/v1/workspaces/invitations/accept", json={"token": invite.json()["token"]}
        )
        assert accepted.status_code == 200
        assert (await client.get(f"{base}/messages")).status_code == 200
        assert (await client.get(f"{base}/memories")).status_code == 200
        denied = await client.post(
            f"{base}/memories", json={"content": "Viewer cannot add memory."}
        )
        assert denied.status_code == 403

        await client.post("/api/v1/auth/logout")
        await client.post(
            "/api/v1/auth/login",
            json={"email": owner["user"]["email"], "password": "correct-horse-battery-staple"},
        )
        deleted = await client.delete(f"{base}/memories/{memory_id}")
        assert deleted.status_code == 204
        with sessions() as db:
            assert db.scalar(select(AssistantMemory.id)) is None
            assert len(db.scalars(select(AssistantMessage)).all()) == 2
            event_types = db.scalars(select(AuditEvent.event_type)).all()
            assert "assistant.memory_created" in event_types
            assert "assistant.memory_deleted" in event_types
            assert "assistant.message_created" in event_types
            assert viewer["user"]["email"] == "assistant-viewer@example.com"

    asyncio.run(_exercise_api(scenario))


def test_workspace_assistant_requires_a_configured_model(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "openai_model", None)
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "anthropic_model", None)
    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(settings, "gemini_model", None)

    async def scenario(client: httpx.AsyncClient, _: sessionmaker) -> None:
        owner = await _register(client, "assistant-no-model@example.com", "No Model")
        workspace_id = owner["workspaces"][0]["id"]
        response = await client.post(
            f"/api/v1/workspaces/{workspace_id}/assistant/chat",
            json={"prompt": "Summarize the current workspace."},
        )
        assert response.status_code == 503

    asyncio.run(_exercise_api(scenario))


def test_project_task_proposals_are_cited_and_require_user_creation(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("test-provider-key"))
    monkeypatch.setattr(settings, "openai_model", "test-model")
    captured: dict = {}

    async def fake_proposals(prompt, sources, *, provider=None):
        captured.update({"prompt": prompt, "sources": sources, "provider": provider})
        citation = str(sources[0].id)
        return {
            "provider": provider,
            "model": "test-model",
            "summary": "The rollout notes support an implementation and verification task.",
            "needs_more_context": False,
            "tasks": [
                {
                    "title": "Implement refresh token rotation",
                    "description": "Rotate refresh tokens after successful sign-in.",
                    "priority": "high",
                    "labels": ["auth", "security"],
                    "citations": [citation],
                }
            ],
            "usage": {"input_tokens": 25, "output_tokens": 20, "total_tokens": 45},
        }

    monkeypatch.setattr(assistant_api, "propose_native_tasks", fake_proposals)

    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        owner = await _register(client, "task-proposals@example.com", "Proposal Owner")
        workspace_id = owner["workspaces"][0]["id"]
        project = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects",
            json={"name": "Token rollout", "description": "Rotate credentials safely."},
        )
        assert project.status_code == 201
        project_id = project.json()["id"]
        document = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            json={
                "title": "Refresh token rollout notes",
                "content": (
                    "Rotate refresh tokens after successful sign-in and verify reuse protection."
                ),
                "project_id": project_id,
            },
        )
        assert document.status_code == 201

        base = f"/api/v1/workspaces/{workspace_id}/assistant/projects/{project_id}/task-proposals"
        response = await client.post(
            base,
            json={
                "prompt": "Break the refresh token rollout into useful tasks.",
                "provider": "openai",
            },
        )
        assert response.status_code == 200, response.text
        draft = response.json()
        assert draft["provider"] == "openai"
        assert draft["usage"]["total_tokens"] == 45
        assert draft["tasks"][0]["citations"][0]["id"] == document.json()["id"]
        assert captured["provider"] == "openai"
        assert captured["prompt"].startswith("Break the refresh token")

        task_url = f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks"
        assert (await client.get(task_url)).json() == []
        invalid = draft["tasks"][0] | {"citations": ["00000000-0000-0000-0000-000000000000"]}
        monkeypatch.setattr(
            assistant_api,
            "propose_native_tasks",
            lambda *args, **kwargs: _resolved({**draft, "tasks": [invalid]}),
        )
        rejected = await client.post(
            base,
            json={"prompt": "Break the refresh token rollout into useful tasks."},
        )
        assert rejected.status_code == 502
        assert (await client.get(task_url)).json() == []

        created = await client.post(
            task_url,
            json={
                "title": draft["tasks"][0]["title"],
                "description": draft["tasks"][0]["description"],
                "priority": draft["tasks"][0]["priority"],
                "labels": draft["tasks"][0]["labels"],
            },
        )
        assert created.status_code == 201
        assert created.json()["title"] == "Implement refresh token rotation"
        with sessions() as db:
            event_types = db.scalars(select(AuditEvent.event_type)).all()
            assert "assistant.task_proposals_generated" in event_types

    async def _resolved(value):
        return value

    asyncio.run(_exercise_api(scenario))
