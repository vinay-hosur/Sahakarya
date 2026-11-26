import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
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


def test_member_can_create_read_and_update_native_project() -> None:
    async def scenario(client: httpx.AsyncClient, test_sessions: sessionmaker) -> None:
        registered = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "project-owner@example.com",
                "display_name": "Project Owner",
                "password": "correct-horse-battery-staple",
            },
        )
        assert registered.status_code == 201
        workspace_id = registered.json()["workspaces"][0]["id"]
        base = f"/api/v1/workspaces/{workspace_id}/projects"

        created = await client.post(
            base,
            json={"name": "Authentication", "description": "Resolve the sign-in issue."},
        )
        assert created.status_code == 201
        project = created.json()
        assert project["name"] == "Authentication"
        assert project["description"] == "Resolve the sign-in issue."
        assert project["status"] == "active"
        assert project["workspace_id"] == workspace_id

        listed = await client.get(base)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [project["id"]]

        fetched = await client.get(f"{base}/{project['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "Authentication"

        updated = await client.patch(
            f"{base}/{project['id']}",
            json={"name": "Authentication reliability", "status": "on_hold"},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Authentication reliability"
        assert updated.json()["status"] == "on_hold"
        assert updated.json()["description"] == "Resolve the sign-in issue."

        with test_sessions() as db:
            events = db.scalars(
                select(AuditEvent.event_type)
                .where(AuditEvent.workspace_id == UUID(workspace_id))
                .order_by(AuditEvent.created_at)
            ).all()
            assert "project.created" in events
            assert "project.updated" in events

    asyncio.run(_exercise_api(scenario))


def test_projects_are_isolated_by_workspace_and_membership() -> None:
    async def scenario(client: httpx.AsyncClient, _: sessionmaker) -> None:
        first = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "first-project-user@example.com",
                "display_name": "First Project User",
                "password": "correct-horse-battery-staple",
            },
        )
        workspace_id = first.json()["workspaces"][0]["id"]
        created = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects", json={"name": "Private project"}
        )
        project_id = created.json()["id"]

        await client.post("/api/v1/auth/logout")
        second = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "second-project-user@example.com",
                "display_name": "Second Project User",
                "password": "correct-horse-battery-staple",
            },
        )
        assert second.status_code == 201

        assert (await client.get(f"/api/v1/workspaces/{workspace_id}/projects")).status_code == 404
        assert (
            await client.get(f"/api/v1/workspaces/{workspace_id}/projects/{project_id}")
        ).status_code == 404

    asyncio.run(_exercise_api(scenario))


def test_project_update_rejects_empty_and_invalid_status() -> None:
    async def scenario(client: httpx.AsyncClient, _: sessionmaker) -> None:
        registered = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "project-validation@example.com",
                "display_name": "Project Validation",
                "password": "correct-horse-battery-staple",
            },
        )
        workspace_id = registered.json()["workspaces"][0]["id"]
        created = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects", json={"name": "Validation project"}
        )
        project_id = created.json()["id"]
        base = f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"

        assert (await client.patch(base, json={})).status_code == 422
        assert (await client.patch(base, json={"status": "unknown"})).status_code == 422

    asyncio.run(_exercise_api(scenario))


def test_workspace_viewer_can_read_projects_but_cannot_write() -> None:
    async def scenario(client: httpx.AsyncClient, test_sessions: sessionmaker) -> None:
        registered = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "project-viewer-owner@example.com",
                "display_name": "Project Owner",
                "password": "correct-horse-battery-staple",
            },
        )
        workspace_id = registered.json()["workspaces"][0]["id"]
        project = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects", json={"name": "Visible project"}
        )
        project_id = project.json()["id"]
        await client.post("/api/v1/auth/logout")
        viewer = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "project-viewer@example.com",
                "display_name": "Project Viewer",
                "password": "correct-horse-battery-staple",
            },
        )
        viewer_id = UUID(viewer.json()["user"]["id"])
        with test_sessions() as db:
            db.add(
                WorkspaceMember(workspace_id=UUID(workspace_id), user_id=viewer_id, role="viewer")
            )
            db.commit()
        await client.post("/api/v1/auth/logout")
        await client.post(
            "/api/v1/auth/login",
            json={
                "email": "project-viewer@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        base = f"/api/v1/workspaces/{workspace_id}/projects"
        assert (await client.get(base)).status_code == 200
        assert (await client.get(f"{base}/{project_id}")).status_code == 200
        assert (
            await client.get(f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/overview")
        ).status_code == 200
        assert (await client.post(base, json={"name": "Forbidden"})).status_code == 403
        assert (
            await client.patch(f"{base}/{project_id}", json={"status": "completed"})
        ).status_code == 403

    asyncio.run(_exercise_api(scenario))


def test_project_overview_reports_stored_task_and_decision_signals() -> None:
    async def scenario(client: httpx.AsyncClient, _: sessionmaker) -> None:
        registered = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "project-overview@example.com",
                "display_name": "Project Overview",
                "password": "correct-horse-battery-staple",
            },
        )
        workspace_id = registered.json()["workspaces"][0]["id"]
        project = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects", json={"name": "Overview project"}
        )
        project_id = project.json()["id"]
        task_base = f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks"
        overdue = await client.post(
            task_base,
            json={
                "title": "Overdue urgent work",
                "priority": "urgent",
                "due_date": (date.today() - timedelta(days=1)).isoformat(),
            },
        )
        assert overdue.status_code == 201
        blocked = await client.post(task_base, json={"title": "Blocked task"})
        assert blocked.status_code == 201
        updated = await client.patch(
            f"{task_base}/{blocked.json()['id']}", json={"status": "blocked"}
        )
        assert updated.status_code == 200
        due_soon = await client.post(
            task_base,
            json={"title": "Due soon", "due_date": (date.today() + timedelta(days=2)).isoformat()},
        )
        assert due_soon.status_code == 201
        decision = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/decisions",
            json={"title": "Use short sessions", "description": "Reduce token lifetime."},
        )
        assert decision.status_code == 201

        response = await client.get(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/overview"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_tasks"] == 3
        assert payload["status_counts"] == {"todo": 2, "in_progress": 0, "blocked": 1, "done": 0}
        assert [task["id"] for task in payload["overdue_tasks"]] == [overdue.json()["id"]]
        assert [task["id"] for task in payload["due_soon_tasks"]] == [due_soon.json()["id"]]
        assert [task["id"] for task in payload["blocked_tasks"]] == [blocked.json()["id"]]
        assert payload["high_priority_tasks"][0]["id"] == overdue.json()["id"]
        assert payload["recent_decisions"][0]["id"] == decision.json()["id"]

    asyncio.run(_exercise_api(scenario))
