import asyncio
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AuthSession
from app.models.entities import User


async def _exercise_api(
    action: Callable[[httpx.AsyncClient], Awaitable[None]],
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
            await action(client)
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_register_workspace_session_and_logout() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        unauthenticated = await client.get("/api/v1/workspaces")
        assert unauthenticated.status_code == 401

        registered = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "owner@example.com",
                "display_name": "Workspace Owner",
                "password": "correct-horse-battery-staple",
            },
        )
        assert registered.status_code == 201
        user = registered.json()["user"]
        assert user["email"] == "owner@example.com"
        assert len(registered.json()["workspaces"]) == 1
        assert "httponly" in registered.headers["set-cookie"].lower()

        whoami = await client.get("/api/v1/auth/me")
        assert whoami.status_code == 200
        assert whoami.json()["user"]["id"] == user["id"]

        created = await client.post("/api/v1/workspaces", json={"name": "Product Team"})
        assert created.status_code == 201
        workspace = created.json()
        assert workspace["role"] == "owner"

        read = await client.get(f"/api/v1/workspaces/{workspace['id']}")
        assert read.status_code == 200
        assert read.json()["name"] == "Product Team"

        renamed = await client.patch(
            f"/api/v1/workspaces/{workspace['id']}", json={"name": "Platform Team"}
        )
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "Platform Team"

        logged_out = await client.post("/api/v1/auth/logout")
        assert logged_out.status_code == 204
        assert (await client.get("/api/v1/auth/me")).status_code == 401

    asyncio.run(_exercise_api(scenario))


def test_password_is_hashed_and_duplicate_registration_is_rejected() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        body = {
            "email": "member@example.com",
            "display_name": "Workspace Member",
            "password": "long-enough-password",
        }
        registered = await client.post("/api/v1/auth/register", json=body)
        assert registered.status_code == 201
        duplicate = await client.post("/api/v1/auth/register", json=body)
        assert duplicate.status_code == 409

        test_db = next(app.dependency_overrides[get_db]())
        try:
            user = test_db.scalar(select(User).where(User.email == body["email"]))
            assert user is not None
            assert user.password_hash != body["password"]
            assert user.password_hash.startswith("$argon2")
            assert test_db.scalar(select(AuthSession)) is not None
        finally:
            test_db.close()

    asyncio.run(_exercise_api(scenario))


def test_invalid_credentials_and_foreign_workspace_access_are_rejected() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        first = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "first@example.com",
                "display_name": "First Person",
                "password": "correct-horse-battery-staple",
            },
        )
        assert first.status_code == 201
        private_workspace_id = first.json()["workspaces"][0]["id"]

        wrong = await client.post(
            "/api/v1/auth/login",
            json={"email": "first@example.com", "password": "wrong-password"},
        )
        assert wrong.status_code == 401

        await client.post("/api/v1/auth/logout")
        second = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "second@example.com",
                "display_name": "Second Person",
                "password": "correct-horse-battery-staple",
            },
        )
        assert second.status_code == 201
        inaccessible = await client.get(f"/api/v1/workspaces/{private_workspace_id}")
        assert inaccessible.status_code == 404

    asyncio.run(_exercise_api(scenario))
