import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import AuditEvent, WorkspaceMember


async def _exercise_api(
    action: Callable[[httpx.AsyncClient, sessionmaker], Awaitable[None]],
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
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await action(client, test_sessions)
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


async def _register(client: httpx.AsyncClient, email: str) -> str:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "display_name": "Decision User",
            "password": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 201
    return response.json()["workspaces"][0]["id"]


def test_decision_create_edit_references_history_and_audit() -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        workspace_id = await _register(client, "decision-owner@example.com")
        project = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects", json={"name": "Decision project"}
        )
        project_id = project.json()["id"]
        document = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            json={"title": "Auth RFC", "content": "The refresh token rotates."},
        )
        task = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks",
            json={"title": "Implement rotation"},
        )
        base = f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/decisions"
        created = await client.post(
            base,
            json={
                "title": "Rotate refresh tokens",
                "description": "Refresh tokens are replaced after each use.",
                "decision_date": "2026-11-26",
                "reasoning": "Rotation reduces replay risk.",
                "alternatives": ["Long-lived token", "Short-lived token only"],
                "references": [
                    {
                        "label": "Auth RFC",
                        "document_id": document.json()["id"],
                        "notes": "Design rationale",
                    },
                    {"label": "Implementation task", "task_id": task.json()["id"]},
                    {"label": "OWASP guidance", "source_url": "https://example.com/refresh-tokens"},
                ],
            },
        )
        assert created.status_code == 201, created.text
        decision = created.json()
        assert decision["status"] == "proposed"
        assert decision["decision_date"] == "2026-11-26"
        assert len(decision["references"]) == 3

        updated = await client.patch(
            f"{base}/{decision['id']}",
            json={"status": "approved", "reasoning": "Approved after review.", "references": []},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["status"] == "approved"
        assert updated.json()["version_number"] == 2
        assert updated.json()["references"] == []
        assert (
            await client.patch(f"{base}/{decision['id']}", json={"status": "invalid"})
        ).status_code == 422

        listed = await client.get(base, params={"q": "refresh"})
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [decision["id"]]
        history = await client.get(f"{base}/{decision['id']}/history")
        assert history.status_code == 200
        assert [item["version_number"] for item in history.json()] == [2, 1]
        assert len(history.json()[1]["snapshot"]["references"]) == 3

        with sessions() as db:
            events = db.scalars(
                select(AuditEvent.event_type).where(AuditEvent.workspace_id == UUID(workspace_id))
            ).all()
            assert "decision.created" in events
            assert "decision.updated" in events

    asyncio.run(_exercise_api(scenario))


def test_decision_rejects_cross_workspace_evidence_and_membership() -> None:
    async def scenario(client: httpx.AsyncClient, _: sessionmaker) -> None:
        workspace_id = await _register(client, "decision-cross-owner@example.com")
        project = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects", json={"name": "First project"}
        )
        other_workspace = await client.post("/api/v1/workspaces", json={"name": "Other workspace"})
        other_workspace_id = other_workspace.json()["id"]
        foreign_doc = await client.post(
            f"/api/v1/workspaces/{other_workspace_id}/documents",
            json={"title": "Foreign evidence", "content": "Not available to the first project"},
        )
        base = f"/api/v1/workspaces/{workspace_id}/projects/{project.json()['id']}/decisions"
        payload = {"title": "A decision", "description": "Description"}
        foreign_reference = {
            **payload,
            "references": [{"label": "Foreign document", "document_id": foreign_doc.json()["id"]}],
        }
        assert (await client.post(base, json=foreign_reference)).status_code == 422
        assert (
            await client.post(
                base, json={**payload, "owner_user_id": "00000000-0000-0000-0000-000000000000"}
            )
        ).status_code == 422
        await client.post("/api/v1/auth/logout")
        await _register(client, "decision-outsider@example.com")
        assert (await client.get(base)).status_code == 404

    asyncio.run(_exercise_api(scenario))


def test_viewer_can_read_decision_and_history_but_cannot_write() -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        workspace_id = await _register(client, "decision-viewer-owner@example.com")
        project = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects", json={"name": "Viewer project"}
        )
        project_id = project.json()["id"]
        base = f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/decisions"
        decision = await client.post(
            base, json={"title": "Read-only decision", "description": "Text"}
        )
        decision_id = decision.json()["id"]
        await client.post("/api/v1/auth/logout")
        viewer = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "decision-viewer@example.com",
                "display_name": "Decision Viewer",
                "password": "correct-horse-battery-staple",
            },
        )
        viewer_id = UUID(viewer.json()["user"]["id"])
        with sessions() as db:
            db.add(
                WorkspaceMember(workspace_id=UUID(workspace_id), user_id=viewer_id, role="viewer")
            )
            db.commit()
        await client.post("/api/v1/auth/logout")
        await client.post(
            "/api/v1/auth/login",
            json={
                "email": "decision-viewer@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        assert (await client.get(f"{base}/{decision_id}")).status_code == 200
        assert (await client.get(f"{base}/{decision_id}/history")).status_code == 200
        assert (
            await client.patch(f"{base}/{decision_id}", json={"status": "approved"})
        ).status_code == 403
        assert (
            await client.post(base, json={"title": "Denied", "description": "Denied"})
        ).status_code == 403

    asyncio.run(_exercise_api(scenario))
