import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1 import assistant as assistant_api
from app.api.v1 import telegram as telegram_api
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import Integration, TelegramMessage


async def _exercise_api(
    action: Callable[[httpx.AsyncClient, sessionmaker], Awaitable[None]],
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            await action(client, sessions)
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


async def _register(client: httpx.AsyncClient) -> dict:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "telegram-owner@example.com",
            "display_name": "Telegram Owner",
            "password": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_telegram_webhook_scopes_group_and_indexes_new_messages(monkeypatch) -> None:
    monkeypatch.setattr(
        settings, "integration_encryption_key", Fernet.generate_key().decode("ascii")
    )
    monkeypatch.setattr(settings, "public_api_url", "https://api.example.test")
    calls: list[tuple[str, dict]] = []

    async def fake_telegram_api(token: str, method: str, payload: dict | None = None) -> dict:
        assert token == "1234567890:telegram-test-bot-token"
        calls.append((method, payload or {}))
        if method == "getMe":
            return {
                "id": 1234567890,
                "is_bot": True,
                "first_name": "Sahakarya",
                "username": "sahakarya_test_bot",
            }
        return {}

    monkeypatch.setattr(telegram_api, "_telegram_api", fake_telegram_api)

    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        owner = await _register(client)
        workspace_id = owner["workspaces"][0]["id"]
        project = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects", json={"name": "Telegram project"}
        )
        project_id = project.json()["id"]
        connected = await client.post(
            f"/api/v1/workspaces/{workspace_id}/integrations/telegram/connect",
            json={"bot_token": "1234567890:telegram-test-bot-token"},
        )
        assert connected.status_code == 200, connected.text
        assert connected.json()["bot_username"] == "sahakarya_test_bot"
        assert calls[1][0] == "setWebhook"
        webhook_secret = calls[1][1]["secret_token"]
        webhook_url = f"/api/v1/integrations/telegram/webhook/{connected.json()['integration_id']}"
        update = {
            "update_id": 8,
            "message": {
                "message_id": 41,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": -1001234567890, "type": "supergroup", "title": "Test group"},
                "from": {"id": 9901, "first_name": "Vinay", "last_name": "Hosur"},
                "text": "The database migration needs an owner review.",
            },
        }
        bad = await client.post(webhook_url, json=update)
        assert bad.status_code == 403
        headers = {"X-Telegram-Bot-Api-Secret-Token": webhook_secret}
        received = await client.post(webhook_url, json=update, headers=headers)
        assert received.status_code == 200, received.text
        groups = await client.get(f"/api/v1/workspaces/{workspace_id}/integrations/telegram/chats")
        assert groups.status_code == 200
        chat = groups.json()[0]
        assert chat["status"] == "pending"
        with sessions() as db:
            assert db.scalar(select(func.count(TelegramMessage.id))) == 0

        enabled = await client.patch(
            f"/api/v1/workspaces/{workspace_id}/integrations/telegram/chats/{chat['id']}",
            json={"project_id": project_id, "enabled": True},
        )
        assert enabled.status_code == 200
        update["message"]["message_id"] = 42
        update["message"]["text"] = "The database migration needs an owner review before release."
        received = await client.post(webhook_url, json=update, headers=headers)
        assert received.status_code == 200
        duplicate = await client.post(webhook_url, json=update, headers=headers)
        assert duplicate.status_code == 200

        with sessions() as db:
            message = db.scalar(select(TelegramMessage))
            assert message is not None
            assert message.project_id == UUID(project_id)
            assert message.sender_name == "Vinay Hosur"
            assert message.content.startswith("The database migration")
            stored_integration = db.scalar(
                select(Integration).where(Integration.provider == "telegram")
            )
            assert stored_integration is not None
            assert stored_integration.secret_ciphertext != "1234567890:telegram-test-bot-token"

        evidence = assistant_api._workspace_evidence(
            db,
            UUID(workspace_id),
            "What did the team say about the database migration?",
            UUID(project_id),
        )
        telegram_sources = [source for source in evidence if source.provider == "telegram"]
        assert len(telegram_sources) == 1
        assert "owner review" in telegram_sources[0].content_text

        disconnected = await client.delete(
            f"/api/v1/workspaces/{workspace_id}/integrations/telegram"
        )
        assert disconnected.status_code == 204
        with sessions() as db:
            assert db.scalar(select(func.count(TelegramMessage.id))) == 1
            status_view = await client.get(
                f"/api/v1/workspaces/{workspace_id}/integrations/telegram/status"
            )
            assert status_view.json()["status"] == "not_configured"

    asyncio.run(_exercise_api(scenario))


def test_telegram_connect_rejects_invalid_bot_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "integration_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "public_api_url", "https://api.example.test")

    async def invalid_token(_token: str, method: str, _payload: dict | None = None) -> dict:
        if method == "getMe":
            raise ValueError("secret details are not returned")
        return {}

    monkeypatch.setattr(telegram_api, "_telegram_api", invalid_token)

    async def scenario(client: httpx.AsyncClient, _: sessionmaker) -> None:
        owner = await _register(client)
        workspace_id = owner["workspaces"][0]["id"]
        response = await client.post(
            f"/api/v1/workspaces/{workspace_id}/integrations/telegram/connect",
            json={"bot_token": "1234567890:telegram-test-bot-token"},
        )
        assert response.status_code == 422
        assert "1234567890:telegram-test-bot-token" not in response.text

    asyncio.run(_exercise_api(scenario))
