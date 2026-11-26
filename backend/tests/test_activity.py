import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import WorkspaceMember


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
            "display_name": "Activity User",
            "password": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 201
    return response.json()["workspaces"][0]["id"]


def test_activity_timeline_filters_by_project_and_includes_native_events() -> None:
    async def scenario(client: httpx.AsyncClient, _: sessionmaker) -> None:
        workspace_id = await _register(client, "activity-owner@example.com")
        first_project = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects", json={"name": "Activity Alpha"}
        )
        second_project = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects", json={"name": "Activity Beta"}
        )
        first_id = first_project.json()["id"]
        second_id = second_project.json()["id"]
        task_base = f"/api/v1/workspaces/{workspace_id}/projects/{first_id}/tasks"
        task = await client.post(task_base, json={"title": "Activity task"})
        await client.patch(f"{task_base}/{task.json()['id']}", json={"status": "in_progress"})
        await client.post(
            f"{task_base}/{task.json()['id']}/comments", json={"body": "Activity comment"}
        )
        document = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            json={"title": "Activity doc", "content": "Details", "project_id": first_id},
        )
        await client.patch(
            f"/api/v1/workspaces/{workspace_id}/documents/{document.json()['id']}",
            json={"content": "Updated details"},
        )
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{first_id}/decisions",
            json={"title": "Activity decision", "description": "Choose this approach."},
        )
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{second_id}/tasks",
            json={"title": "Other project task"},
        )

        base = f"/api/v1/workspaces/{workspace_id}/activity"
        response = await client.get(base, params={"project_id": first_id, "limit": 100})
        assert response.status_code == 200, response.text
        entries = response.json()
        types = {entry["event_type"] for entry in entries}
        assert {"project.created", "task.created", "task.updated", "task.comment_created"} <= types
        assert {"document.created", "document.updated", "decision.created"} <= types
        assert all(entry["project_id"] == first_id for entry in entries)
        assert all(entry["actor_display_name"] == "Activity User" for entry in entries)
        assert entries == sorted(
            entries, key=lambda entry: (entry["created_at"], entry["id"]), reverse=True
        )
        assert all("Activity comment" not in entry["summary"] for entry in entries)

        page = await client.get(base, params={"project_id": first_id, "limit": 2})
        assert len(page.json()) == 2
        workspace_rows = await client.get(base, params={"limit": 100})
        assert workspace_rows.status_code == 200
        assert any(entry["project_id"] == second_id for entry in workspace_rows.json())

    asyncio.run(_exercise_api(scenario))


def test_activity_requires_membership_and_viewers_can_read() -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        workspace_id = await _register(client, "activity-membership-owner@example.com")
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects", json={"name": "Visible to viewer"}
        )
        base = f"/api/v1/workspaces/{workspace_id}/activity"
        await client.post("/api/v1/auth/logout")
        viewer = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "activity-viewer@example.com",
                "display_name": "Activity Viewer",
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
                "email": "activity-viewer@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        response = await client.get(base)
        assert response.status_code == 200
        assert response.json()
        await client.post("/api/v1/auth/logout")
        await _register(client, "activity-outsider@example.com")
        assert (await client.get(base)).status_code == 404

    asyncio.run(_exercise_api(scenario))
