import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1 import chat as chat_api
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import AuditEvent, ChatMessage, WorkspaceMember


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


async def _register(client: httpx.AsyncClient, email: str, name: str) -> dict:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "display_name": name, "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_workspace_chat_persists_messages_and_enforces_viewer_read_only() -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        owner = await _register(client, "chat-owner@example.com", "Chat Owner")
        workspace_id = owner["workspaces"][0]["id"]
        base = f"/api/v1/workspaces/{workspace_id}/chat"
        created = await client.post(f"{base}/channels", json={"name": "#general"})
        assert created.status_code == 201, created.text
        channel = created.json()
        assert channel["name"] == "general"

        sent = await client.post(
            f"{base}/channels/{channel['id']}/messages", json={"body": "Welcome to the team."}
        )
        assert sent.status_code == 201, sent.text
        assert sent.json()["sender_display_name"] == "Chat Owner"
        history = await client.get(f"{base}/channels/{channel['id']}/messages")
        assert history.status_code == 200
        assert [row["body"] for row in history.json()] == ["Welcome to the team."]

        invite = await client.post(
            f"/api/v1/workspaces/{workspace_id}/invitations",
            json={"email": "chat-viewer@example.com", "role": "viewer"},
        )
        assert invite.status_code == 201
        await client.post("/api/v1/auth/logout")
        viewer = await _register(client, "chat-viewer@example.com", "Chat Viewer")
        await client.post(
            "/api/v1/workspaces/invitations/accept", json={"token": invite.json()["token"]}
        )
        assert (await client.get(f"{base}/channels/{channel['id']}/messages")).status_code == 200
        denied = await client.post(
            f"{base}/channels/{channel['id']}/messages", json={"body": "Viewer write"}
        )
        assert denied.status_code == 403

        with sessions() as db:
            saved = db.scalars(select(ChatMessage)).all()
            event_types = db.scalars(select(AuditEvent.event_type)).all()
            assert len(saved) == 1 and saved[0].sender_user_id == UUID(owner["user"]["id"])
            assert "chat.channel_created" in event_types
            assert "chat.message_created" in event_types
            assert viewer["user"]["email"] == "chat-viewer@example.com"

    asyncio.run(_exercise_api(scenario))


def test_invited_workspace_members_exchange_persistent_chat_messages() -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        owner = await _register(client, "chat-exchange-owner@example.com", "Chat Owner")
        workspace_id = owner["workspaces"][0]["id"]
        invite = await client.post(
            f"/api/v1/workspaces/{workspace_id}/invitations",
            json={"email": "chat-exchange-member@example.com", "role": "member"},
        )
        assert invite.status_code == 201, invite.text
        channel = await client.post(
            f"/api/v1/workspaces/{workspace_id}/chat/channels", json={"name": "general"}
        )
        assert channel.status_code == 201, channel.text
        channel_id = channel.json()["id"]
        chat_path = f"/api/v1/workspaces/{workspace_id}/chat/channels/{channel_id}/messages"
        owner_message = await client.post(chat_path, json={"body": "Welcome to the project."})
        assert owner_message.status_code == 201, owner_message.text

        await client.post("/api/v1/auth/logout")
        member = await _register(client, "chat-exchange-member@example.com", "Chat Member")
        accepted = await client.post(
            "/api/v1/workspaces/invitations/accept", json={"token": invite.json()["token"]}
        )
        assert accepted.status_code == 200, accepted.text
        owner_history = await client.get(chat_path)
        assert [message["body"] for message in owner_history.json()] == ["Welcome to the project."]
        member_message = await client.post(chat_path, json={"body": "Thanks, I can see it."})
        assert member_message.status_code == 201, member_message.text

        await client.post("/api/v1/auth/logout")
        await client.post(
            "/api/v1/auth/login",
            json={
                "email": owner["user"]["email"],
                "password": "correct-horse-battery-staple",
            },
        )
        refreshed_history = await client.get(chat_path)
        assert [message["body"] for message in refreshed_history.json()] == [
            "Welcome to the project.",
            "Thanks, I can see it.",
        ]
        assert refreshed_history.json()[1]["created_at"] > refreshed_history.json()[0]["created_at"]
        assert refreshed_history.json()[1]["sender_user_id"] == member["user"]["id"]
        with sessions() as db:
            saved = db.scalars(select(ChatMessage)).all()
            assert len(saved) == 2

    asyncio.run(_exercise_api(scenario))


def test_workspace_chat_websocket_delivers_teammate_messages(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        with test_sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(chat_api, "SessionLocal", test_sessions)
    try:
        with TestClient(app) as client:
            owner_response = client.post(
                "/api/v1/auth/register",
                json={
                    "email": "chat-websocket-owner@example.com",
                    "display_name": "Chat Owner",
                    "password": "correct-horse-battery-staple",
                },
            )
            assert owner_response.status_code == 201, owner_response.text
            owner_session = client.cookies.get(settings.session_cookie_name)
            workspace_id = owner_response.json()["workspaces"][0]["id"]
            invite_response = client.post(
                f"/api/v1/workspaces/{workspace_id}/invitations",
                json={"email": "chat-websocket-member@example.com", "role": "member"},
            )
            assert invite_response.status_code == 201, invite_response.text
            channel_response = client.post(
                f"/api/v1/workspaces/{workspace_id}/chat/channels", json={"name": "general"}
            )
            assert channel_response.status_code == 201, channel_response.text
            channel_id = channel_response.json()["id"]

            client.post("/api/v1/auth/logout")
            member_response = client.post(
                "/api/v1/auth/register",
                json={
                    "email": "chat-websocket-member@example.com",
                    "display_name": "Chat Member",
                    "password": "correct-horse-battery-staple",
                },
            )
            assert member_response.status_code == 201, member_response.text
            member_session = client.cookies.get(settings.session_cookie_name)
            accepted = client.post(
                "/api/v1/workspaces/invitations/accept",
                json={"token": invite_response.json()["token"]},
            )
            assert accepted.status_code == 200, accepted.text

            client.cookies.set(settings.session_cookie_name, owner_session, domain="testserver")
            websocket_url = (
                f"/api/v1/workspaces/{workspace_id}/chat/ws?channel_id={channel_id}"
            )
            with client.websocket_connect(
                websocket_url, headers={"origin": settings.allowed_origins[0]}
            ) as websocket:
                assert websocket.receive_json()["type"] == "presence"
                client.cookies.set(
                    settings.session_cookie_name, member_session, domain="testserver"
                )
                sent = client.post(
                    f"/api/v1/workspaces/{workspace_id}/chat/channels/{channel_id}/messages",
                    json={"body": "Teammate message reached the live channel."},
                )
                assert sent.status_code == 201, sent.text
                event = websocket.receive_json()
                assert event["type"] == "message"
                assert event["message"]["body"] == "Teammate message reached the live channel."
                assert event["message"]["sender_display_name"] == "Chat Member"
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_workspace_chat_mentions_notify_only_the_named_member() -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        owner = await _register(client, "mention-owner@example.com", "Mention Owner")
        workspace_id = owner["workspaces"][0]["id"]
        await client.post("/api/v1/auth/logout")
        teammate = await _register(client, "teammate@example.com", "Mention Teammate")
        with sessions() as db:
            db.add(
                WorkspaceMember(
                    workspace_id=UUID(workspace_id),
                    user_id=UUID(teammate["user"]["id"]),
                    role="member",
                )
            )
            db.commit()

        await client.post("/api/v1/auth/logout")
        await client.post(
            "/api/v1/auth/login",
            json={"email": owner["user"]["email"], "password": "correct-horse-battery-staple"},
        )
        chat_base = f"/api/v1/workspaces/{workspace_id}/chat"
        channel = await client.post(f"{chat_base}/channels", json={"name": "general"})
        assert channel.status_code == 201
        sent = await client.post(
            f"{chat_base}/channels/{channel.json()['id']}/messages",
            json={"body": "@teammate@example.com please review the release notes"},
        )
        assert sent.status_code == 201

        await client.post("/api/v1/auth/logout")
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": teammate["user"]["email"],
                "password": "correct-horse-battery-staple",
            },
        )
        assert login.status_code == 200
        notification_base = f"/api/v1/workspaces/{workspace_id}/notifications"
        unread = await client.get(f"{notification_base}?unread_only=true")
        assert unread.status_code == 200
        assert len(unread.json()) == 1
        notification = unread.json()[0]
        assert notification["event_type"] == "mention"
        assert "Mention Owner mentioned you in #general" == notification["title"]
        marked = await client.patch(f"{notification_base}/{notification['id']}/read")
        assert marked.status_code == 200
        assert marked.json()["read_at"] is not None
        assert (await client.get(f"{notification_base}?unread_only=true")).json() == []

    asyncio.run(_exercise_api(scenario))
