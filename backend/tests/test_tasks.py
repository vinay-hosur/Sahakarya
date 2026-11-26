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


async def _register(client: httpx.AsyncClient, email: str) -> tuple[str, str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "display_name": "Task User",
            "password": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 201
    workspace_id = response.json()["workspaces"][0]["id"]
    project = await client.post(
        f"/api/v1/workspaces/{workspace_id}/projects", json={"name": "Task project"}
    )
    assert project.status_code == 201
    return workspace_id, project.json()["id"]


def test_member_can_create_read_and_update_tasks_with_audit() -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        workspace_id, project_id = await _register(client, "task-owner@example.com")
        base = f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks"
        created = await client.post(
            base,
            json={
                "title": "Write the onboarding guide",
                "description": "Document the first run.",
                "priority": "high",
                "due_date": "2026-12-10",
            },
        )
        assert created.status_code == 201
        task = created.json()
        assert task["status"] == "todo"
        assert task["priority"] == "high"
        assert task["due_date"] == "2026-12-10"

        listed = await client.get(base)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [task["id"]]
        fetched = await client.get(f"{base}/{task['id']}")
        assert fetched.status_code == 200

        updated = await client.patch(
            f"{base}/{task['id']}", json={"status": "in_progress", "due_date": None}
        )
        assert updated.status_code == 200
        assert updated.json()["status"] == "in_progress"
        assert updated.json()["due_date"] is None
        comment_response = await client.post(
            f"{base}/{task['id']}/comments", json={"body": "  I will take this next.  "}
        )
        assert comment_response.status_code == 201
        assert comment_response.json()["body"] == "I will take this next."
        assert comment_response.json()["author_display_name"] == "Task User"
        comments = await client.get(f"{base}/{task['id']}/comments")
        assert comments.status_code == 200
        assert [item["id"] for item in comments.json()] == [comment_response.json()["id"]]
        assert (
            await client.post(f"{base}/{task['id']}/comments", json={"body": "  "})
        ).status_code == 422

        with sessions() as db:
            events = db.scalars(
                select(AuditEvent.event_type)
                .where(AuditEvent.workspace_id == UUID(workspace_id))
                .order_by(AuditEvent.created_at)
            ).all()
            assert "task.created" in events
            assert "task.updated" in events
            assert "task.comment_created" in events
            update_event = db.scalar(
                select(AuditEvent).where(AuditEvent.event_type == "task.updated")
            )
            assert update_event is not None
            assert update_event.details_json["previous"]["due_date"] == "2026-12-10"

    asyncio.run(_exercise_api(scenario))


def test_task_assignee_must_be_a_workspace_member() -> None:
    async def scenario(client: httpx.AsyncClient, _: sessionmaker) -> None:
        workspace_id, project_id = await _register(client, "task-assignee@example.com")
        await client.post("/api/v1/auth/logout")
        outsider = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "task-outsider@example.com",
                "display_name": "Outside User",
                "password": "correct-horse-battery-staple",
            },
        )
        outsider_id = outsider.json()["user"]["id"]
        await client.post("/api/v1/auth/logout")
        owner_login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "task-assignee@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        assert owner_login.status_code == 200
        response = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks",
            json={"title": "Assign only to a member", "assignee_id": outsider_id},
        )
        assert response.status_code == 422

    asyncio.run(_exercise_api(scenario))


def test_viewer_can_read_but_cannot_change_project_tasks() -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        workspace_id, project_id = await _register(client, "task-viewer-owner@example.com")
        created = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks",
            json={"title": "Existing task"},
        )
        task_id = created.json()["id"]
        base = f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks"
        comment = await client.post(
            f"{base}/{task_id}/comments", json={"body": "Visible to viewers"}
        )
        assert comment.status_code == 201
        viewer = await client.post("/api/v1/auth/logout")
        assert viewer.status_code == 204
        viewer = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "task-viewer@example.com",
                "display_name": "Read Only Viewer",
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
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "task-viewer@example.com", "password": "correct-horse-battery-staple"},
        )
        assert login.status_code == 200
        assert (await client.get(base)).status_code == 200
        assert (await client.patch(f"{base}/{task_id}", json={"status": "done"})).status_code == 403
        assert (await client.post(base, json={"title": "Unauthorized write"})).status_code == 403
        comments = await client.get(f"{base}/{task_id}/comments")
        assert comments.status_code == 200
        assert comments.json()[0]["body"] == "Visible to viewers"
        assert (
            await client.post(f"{base}/{task_id}/comments", json={"body": "No write"})
        ).status_code == 403

    asyncio.run(_exercise_api(scenario))


def test_member_can_delete_task_and_viewer_cannot() -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        workspace_id, project_id = await _register(client, "task-delete-owner@example.com")
        base = f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks"
        task = await client.post(base, json={"title": "Remove obsolete task"})
        assert task.status_code == 201
        task_id = task.json()["id"]
        forbidden_workspace_id = workspace_id

        await client.post("/api/v1/auth/logout")
        viewer = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "task-delete-viewer@example.com",
                "display_name": "Task Viewer",
                "password": "correct-horse-battery-staple",
            },
        )
        viewer_id = UUID(viewer.json()["user"]["id"])
        with sessions() as db:
            db.add(
                WorkspaceMember(
                    workspace_id=UUID(forbidden_workspace_id), user_id=viewer_id, role="viewer"
                )
            )
            db.commit()
        await client.post("/api/v1/auth/logout")
        await client.post(
            "/api/v1/auth/login",
            json={
                "email": "task-delete-viewer@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        delete_url = f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks/{task_id}"
        assert (await client.delete(delete_url)).status_code == 403
        await client.post("/api/v1/auth/logout")
        await client.post(
            "/api/v1/auth/login",
            json={
                "email": "task-delete-owner@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        assert (await client.delete(delete_url)).status_code == 204
        assert (await client.get(delete_url)).status_code == 404
        with sessions() as db:
            event = db.scalar(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == UUID(workspace_id),
                    AuditEvent.event_type == "task.deleted",
                )
            )
            assert event is not None
            assert event.details_json["title"] == "Remove obsolete task"

    asyncio.run(_exercise_api(scenario))


def test_task_endpoints_reject_invalid_payload_and_scope() -> None:
    async def scenario(client: httpx.AsyncClient, _: sessionmaker) -> None:
        workspace_id, project_id = await _register(client, "task-validation@example.com")
        base = f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks"
        assert (await client.post(base, json={"title": " "})).status_code == 422
        assert (
            await client.post(base, json={"title": "Bad priority", "priority": "critical"})
        ).status_code == 422
        created = await client.post(base, json={"title": "Scope test"})
        task_id = created.json()["id"]
        await client.post("/api/v1/auth/logout")
        await _register(client, "task-other-workspace@example.com")
        assert (await client.get(f"{base}/{task_id}")).status_code == 404

    asyncio.run(_exercise_api(scenario))


def test_task_labels_dependencies_and_immutable_history() -> None:
    async def scenario(client: httpx.AsyncClient, _: sessionmaker) -> None:
        workspace_id, project_id = await _register(client, "task-history@example.com")
        base = f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks"
        prerequisite = await client.post(
            base,
            json={"title": "Prepare design", "labels": ["  Platform ", "platform", "ready"]},
        )
        assert prerequisite.status_code == 201
        prerequisite_id = prerequisite.json()["id"]
        assert prerequisite.json()["labels"] == ["Platform", "ready"]

        dependent = await client.post(
            base,
            json={"title": "Implement design", "depends_on_task_ids": [prerequisite_id]},
        )
        assert dependent.status_code == 201
        dependent_id = dependent.json()["id"]
        assert dependent.json()["depends_on_task_ids"] == [prerequisite_id]

        cycle = await client.patch(
            f"{base}/{prerequisite_id}", json={"depends_on_task_ids": [dependent_id]}
        )
        assert cycle.status_code == 422
        cannot_delete = await client.delete(f"{base}/{prerequisite_id}")
        assert cannot_delete.status_code == 409

        updated = await client.patch(
            f"{base}/{dependent_id}",
            json={"labels": ["frontend", "P1"], "status": "in_progress"},
        )
        assert updated.status_code == 200
        assert updated.json()["labels"] == ["frontend", "P1"]
        history = await client.get(f"{base}/{dependent_id}/history")
        assert history.status_code == 200
        assert [entry["event_type"] for entry in history.json()] == ["updated", "created"]
        assert history.json()[0]["actor_display_name"] == "Task User"
        assert history.json()[0]["changes"]["labels"] == {
            "before": [],
            "after": ["frontend", "P1"],
        }

        assert (
            await client.patch(f"{base}/{dependent_id}", json={"depends_on_task_ids": []})
        ).status_code == 200
        assert (await client.delete(f"{base}/{prerequisite_id}")).status_code == 204

    asyncio.run(_exercise_api(scenario))


def test_task_assignment_and_comments_notify_the_assigned_teammate() -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        workspace_id, project_id = await _register(client, "notification-owner@example.com")
        await client.post("/api/v1/auth/logout")
        teammate = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "assigned-teammate@example.com",
                "display_name": "Assigned Teammate",
                "password": "correct-horse-battery-staple",
            },
        )
        teammate_id = UUID(teammate.json()["user"]["id"])
        with sessions() as db:
            db.add(
                WorkspaceMember(workspace_id=UUID(workspace_id), user_id=teammate_id, role="member")
            )
            db.commit()
        await client.post("/api/v1/auth/logout")
        owner_login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "notification-owner@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        assert owner_login.status_code == 200
        task_base = f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks"
        task = await client.post(
            task_base,
            json={"title": "Review launch checklist", "assignee_id": str(teammate_id)},
        )
        assert task.status_code == 201
        comment = await client.post(
            f"{task_base}/{task.json()['id']}/comments",
            json={"body": "The final release notes are ready."},
        )
        assert comment.status_code == 201

        await client.post("/api/v1/auth/logout")
        teammate_login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "assigned-teammate@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        assert teammate_login.status_code == 200
        notification_base = f"/api/v1/workspaces/{workspace_id}/notifications"
        rows = await client.get(notification_base)
        assert rows.status_code == 200
        assert {row["event_type"] for row in rows.json()} == {"task.assigned", "task.comment"}
        titles = {row["title"] for row in rows.json()}
        assert "You were assigned Review launch checklist" in titles
        assert "New comment on Review launch checklist" in titles
        assert (await client.post(f"{notification_base}/read-all")).status_code == 204
        unread = await client.get(f"{notification_base}?unread_only=true")
        assert unread.status_code == 200
        assert unread.json() == []

    asyncio.run(_exercise_api(scenario))
