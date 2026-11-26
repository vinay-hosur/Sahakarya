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
from app.models.entities import AuditEvent, WorkspaceInvitation, WorkspaceMember


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


async def _register(
    client: httpx.AsyncClient, email: str, display_name: str = "Workspace Owner"
) -> dict:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "display_name": display_name,
            "password": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_owner_invites_matching_user_and_invitation_is_one_time() -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        owner = await _register(client, "member-owner@example.com")
        workspace_id = owner["workspaces"][0]["id"]
        created = await client.post(
            f"/api/v1/workspaces/{workspace_id}/invitations",
            json={"email": "NEW.MEMBER@example.com", "role": "viewer"},
        )
        assert created.status_code == 201, created.text
        invite = created.json()
        assert invite["email"] == "new.member@example.com"
        assert len(invite["token"]) >= 32
        with sessions() as db:
            stored = db.get(WorkspaceInvitation, UUID(invite["id"]))
            assert stored is not None
            assert stored.token_hash != invite["token"]
            assert stored.token_hash

        invitations = await client.get(f"/api/v1/workspaces/{workspace_id}/invitations")
        assert invitations.status_code == 200
        assert len(invitations.json()) == 1
        assert "token" not in invitations.json()[0]
        assert (
            await client.post(
                f"/api/v1/workspaces/{workspace_id}/invitations",
                json={"email": "new.member@example.com", "role": "member"},
            )
        ).status_code == 409

        await client.post("/api/v1/auth/logout")
        invitee = await _register(client, "new.member@example.com", "New Member")
        accepted = await client.post(
            "/api/v1/workspaces/invitations/accept", json={"token": invite["token"]}
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["role"] == "viewer"
        assert accepted.json()["email"] == "new.member@example.com"
        assert any(
            row["id"] == workspace_id
            for row in (await client.get("/api/v1/auth/me")).json()["workspaces"]
        )
        assert (
            await client.post(
                "/api/v1/workspaces/invitations/accept", json={"token": invite["token"]}
            )
        ).status_code == 404
        with sessions() as db:
            member = db.get(WorkspaceMember, (UUID(workspace_id), UUID(invitee["user"]["id"])))
            assert member is not None and member.role == "viewer"
            event_types = db.scalars(select(AuditEvent.event_type)).all()
            assert "workspace.member_invited" in event_types
            assert "workspace.member_joined" in event_types

    asyncio.run(_exercise_api(scenario))


def test_owner_can_change_roles_and_remove_members_but_not_owner() -> None:
    async def scenario(client: httpx.AsyncClient, _: sessionmaker) -> None:
        owner = await _register(client, "member-role-owner@example.com")
        workspace_id = owner["workspaces"][0]["id"]
        member = await client.post(
            f"/api/v1/workspaces/{workspace_id}/invitations",
            json={"email": "role-change@example.com", "role": "viewer"},
        )
        await client.post("/api/v1/auth/logout")
        invitee = await _register(client, "role-change@example.com", "Role Change")
        member_id = invitee["user"]["id"]
        await client.post(
            "/api/v1/workspaces/invitations/accept", json={"token": member.json()["token"]}
        )
        await client.post("/api/v1/auth/logout")
        await client.post(
            "/api/v1/auth/login",
            json={
                "email": "member-role-owner@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        member_base = f"/api/v1/workspaces/{workspace_id}/members"
        rows = await client.get(member_base)
        assert rows.status_code == 200
        assert {row["role"] for row in rows.json()} == {"owner", "viewer"}
        changed = await client.patch(f"{member_base}/{member_id}", json={"role": "member"})
        assert changed.status_code == 200
        assert changed.json()["role"] == "member"
        assert (
            await client.patch(f"{member_base}/{owner['user']['id']}", json={"role": "viewer"})
        ).status_code == 409
        assert (await client.delete(f"{member_base}/{owner['user']['id']}")).status_code == 409
        assert (await client.delete(f"{member_base}/{member_id}")).status_code == 204
        assert all(row["user_id"] != member_id for row in (await client.get(member_base)).json())

    asyncio.run(_exercise_api(scenario))


def test_non_owner_cannot_invite_change_roles_or_remove_members() -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        owner = await _register(client, "member-auth-owner@example.com")
        workspace_id = owner["workspaces"][0]["id"]
        await client.post("/api/v1/auth/logout")
        member = await _register(client, "member-authorized@example.com", "Regular Member")
        member_id = UUID(member["user"]["id"])
        with sessions() as db:
            db.add(
                WorkspaceMember(workspace_id=UUID(workspace_id), user_id=member_id, role="member")
            )
            db.commit()
        await client.post("/api/v1/auth/logout")
        await client.post(
            "/api/v1/auth/login",
            json={
                "email": "member-authorized@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        members_url = f"/api/v1/workspaces/{workspace_id}/members"
        invitations_url = f"/api/v1/workspaces/{workspace_id}/invitations"
        assert (await client.get(members_url)).status_code == 200
        assert (
            await client.post(invitations_url, json={"email": "x@example.com"})
        ).status_code == 403
        assert (await client.get(invitations_url)).status_code == 403
        assert (
            await client.patch(f"{members_url}/{owner['user']['id']}", json={"role": "viewer"})
        ).status_code == 403
        assert (await client.delete(f"{members_url}/{owner['user']['id']}")).status_code == 403
        assert (
            await client.get("/api/v1/workspaces/00000000-0000-0000-0000-000000000000/members")
        ).status_code == 404

    asyncio.run(_exercise_api(scenario))


def test_invitation_requires_matching_email_and_owner_role_is_not_invitable() -> None:
    async def scenario(client: httpx.AsyncClient, _: sessionmaker) -> None:
        owner = await _register(client, "invite-owner@example.com")
        workspace_id = owner["workspaces"][0]["id"]
        invitation = await client.post(
            f"/api/v1/workspaces/{workspace_id}/invitations", json={"email": "expected@example.com"}
        )
        assert invitation.status_code == 201
        assert (
            await client.post(
                f"/api/v1/workspaces/{workspace_id}/invitations",
                json={"email": "owner-role@example.com", "role": "owner"},
            )
        ).status_code == 422
        await client.post("/api/v1/auth/logout")
        await _register(client, "other@example.com", "Wrong Email")
        assert (
            await client.post(
                "/api/v1/workspaces/invitations/accept", json={"token": invitation.json()["token"]}
            )
        ).status_code == 403

    asyncio.run(_exercise_api(scenario))
