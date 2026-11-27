import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1 import documents as documents_api
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import AuditEvent, KnowledgeDocumentFile, WorkspaceMember


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
            "display_name": "Knowledge User",
            "password": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 201
    return response.json()["workspaces"][0]["id"]


def test_document_create_update_versions_comments_and_audit() -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        workspace_id = await _register(client, "knowledge-owner@example.com")
        project_response = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects",
            json={"name": "Knowledge project"},
        )
        project_id = project_response.json()["id"]
        base = f"/api/v1/workspaces/{workspace_id}/documents"
        created = await client.post(
            base,
            json={
                "title": "Authentication design",
                "content": "Use short lived access tokens.",
                "category": "Architecture",
                "tags": [" Auth ", "auth", "backend"],
                "project_id": project_id,
            },
        )
        assert created.status_code == 201, created.text
        document = created.json()
        assert document["owner_display_name"] == "Knowledge User"
        assert document["version_number"] == 1
        assert document["tags"] == ["Auth", "backend"]

        listed = await client.get(base, params={"project_id": project_id, "tag": "Auth"})
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [document["id"]]
        assert (await client.get(f"{base}/{document['id']}")).json()["content"] == created.json()[
            "content"
        ]

        updated = await client.patch(
            f"{base}/{document['id']}",
            json={
                "content": "Use short lived tokens and rotate refresh tokens.",
                "tags": ["backend", "security"],
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["version_number"] == 2
        assert updated.json()["updated_at"] >= document["updated_at"]
        assert (
            await client.patch(
                f"{base}/{document['id']}", json={"content": updated.json()["content"]}
            )
        ).status_code == 422

        versions = await client.get(f"{base}/{document['id']}/versions")
        assert versions.status_code == 200
        assert [item["version_number"] for item in versions.json()] == [2, 1]
        assert versions.json()[1]["snapshot"]["content"] == "Use short lived access tokens."

        comment = await client.post(
            f"{base}/{document['id']}/comments", json={"body": "  Reviewed by security. "}
        )
        assert comment.status_code == 201
        assert comment.json()["body"] == "Reviewed by security."
        comments = await client.get(f"{base}/{document['id']}/comments")
        assert comments.status_code == 200
        assert comments.json()[0]["author_display_name"] == "Knowledge User"

        with sessions() as db:
            events = db.scalars(
                select(AuditEvent.event_type).where(AuditEvent.workspace_id == UUID(workspace_id))
            ).all()
            assert "document.created" in events
            assert "document.updated" in events
            assert "document.comment_created" in events

    asyncio.run(_exercise_api(scenario))


def test_documents_enforce_workspace_and_project_isolation() -> None:
    async def scenario(client: httpx.AsyncClient, _: sessionmaker) -> None:
        first_workspace = await _register(client, "knowledge-first@example.com")
        first_project = await client.post(
            f"/api/v1/workspaces/{first_workspace}/projects", json={"name": "First project"}
        )
        first_project_id = first_project.json()["id"]
        first_docs = f"/api/v1/workspaces/{first_workspace}/documents"
        document = await client.post(
            first_docs,
            json={
                "title": "Private notes",
                "content": "First workspace only",
                "project_id": first_project_id,
            },
        )
        document_id = document.json()["id"]

        await client.post("/api/v1/auth/logout")
        second_workspace = await _register(client, "knowledge-second@example.com")
        second_project = await client.post(
            f"/api/v1/workspaces/{second_workspace}/projects", json={"name": "Second project"}
        )
        second_docs = f"/api/v1/workspaces/{second_workspace}/documents"
        assert (await client.get(f"{first_docs}/{document_id}")).status_code == 404
        assert (
            await client.get(f"{first_docs}/search", params={"q": "Private notes"})
        ).status_code == 404
        assert (
            await client.post(
                second_docs,
                json={
                    "title": "Invalid project link",
                    "content": "A cross-workspace association must fail.",
                    "project_id": first_project_id,
                },
            )
        ).status_code == 404
        assert (await client.get(f"{second_docs}?project_id={first_project_id}")).status_code == 404
        assert second_project.status_code == 201

    asyncio.run(_exercise_api(scenario))


def test_only_document_owner_or_workspace_owner_can_edit() -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        workspace_id = await _register(client, "knowledge-doc-owner@example.com")
        base = f"/api/v1/workspaces/{workspace_id}/documents"
        document = await client.post(
            base, json={"title": "Owned document", "content": "Original text"}
        )
        document_id = document.json()["id"]

        await client.post("/api/v1/auth/logout")
        member_response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "knowledge-member@example.com",
                "display_name": "Knowledge Member",
                "password": "correct-horse-battery-staple",
            },
        )
        member_id = UUID(member_response.json()["user"]["id"])
        with sessions() as db:
            db.add(
                WorkspaceMember(workspace_id=UUID(workspace_id), user_id=member_id, role="member")
            )
            db.commit()
        await client.post("/api/v1/auth/logout")
        await client.post(
            "/api/v1/auth/login",
            json={
                "email": "knowledge-member@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        assert (await client.get(f"{base}/{document_id}")).status_code == 200
        assert (
            await client.patch(f"{base}/{document_id}", json={"content": "Changed"})
        ).status_code == 403
        comment = await client.post(
            f"{base}/{document_id}/comments", json={"body": "Member can comment"}
        )
        assert comment.status_code == 201

    asyncio.run(_exercise_api(scenario))


def test_viewer_can_read_documents_but_cannot_create_or_comment() -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        workspace_id = await _register(client, "knowledge-viewer-owner@example.com")
        base = f"/api/v1/workspaces/{workspace_id}/documents"
        document = await client.post(
            base, json={"title": "Viewer document", "content": "Read only"}
        )
        document_id = document.json()["id"]
        await client.post("/api/v1/auth/logout")
        viewer = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "knowledge-viewer@example.com",
                "display_name": "Knowledge Viewer",
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
                "email": "knowledge-viewer@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        assert (await client.get(base)).status_code == 200
        assert (await client.get(f"{base}/{document_id}/versions")).status_code == 200
        assert (await client.get(f"{base}/search", params={"q": "read only"})).status_code == 200
        assert (
            await client.post(base, json={"title": "No write", "content": "Denied"})
        ).status_code == 403
        assert (
            await client.post(f"{base}/{document_id}/comments", json={"body": "Denied"})
        ).status_code == 403
        assert (
            await client.post(
                f"{base}/upload",
                files={"file": ("viewer.txt", b"Viewer cannot upload", "text/plain")},
            )
        ).status_code == 403

    asyncio.run(_exercise_api(scenario))


def test_markdown_upload_download_search_answer_and_audit(monkeypatch) -> None:
    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        workspace_id = await _register(client, "knowledge-upload@example.com")
        project = await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects", json={"name": "Docs project"}
        )
        project_id = project.json()["id"]
        base = f"/api/v1/workspaces/{workspace_id}/documents"
        source_text = (
            "# Authentication design\n\n"
            "Access tokens expire after fifteen minutes. Refresh token rotation is required "
            "after each successful session renewal."
        )
        uploaded = await client.post(
            f"{base}/upload",
            data={
                "title": "Authentication design notes",
                "category": "Architecture",
                "project_id": project_id,
                "tags": "security, auth",
            },
            files={"file": ("authentication.md", source_text.encode(), "text/markdown")},
        )
        assert uploaded.status_code == 201, uploaded.text
        document = uploaded.json()
        assert document["source_filename"] == "authentication.md"
        assert document["content"].startswith("# Authentication design")
        assert document["version_number"] == 1

        search = await client.get(
            f"{base}/search", params={"q": "refresh token rotation", "project_id": project_id}
        )
        assert search.status_code == 200, search.text
        assert search.json()[0]["document_id"] == document["id"]
        assert "rotation" in search.json()[0]["excerpt"].lower()
        assert search.json()[0]["source_filename"] == "authentication.md"

        downloaded = await client.get(f"{base}/{document['id']}/file")
        assert downloaded.status_code == 200
        assert downloaded.content == source_text.encode()
        assert "filename*=UTF-8''authentication.md" in downloaded.headers["content-disposition"]

        answer = await client.post(
            f"{base}/search/answer",
            json={"query": "When does token rotation happen?", "project_id": project_id},
        )
        assert answer.status_code == 200, answer.text
        assert answer.json()["answer"]["citation_ids"] == [document["id"]]
        with sessions() as db:
            events = db.scalars(
                select(AuditEvent.event_type).where(AuditEvent.workspace_id == UUID(workspace_id))
            ).all()
            assert "document.uploaded" in events
            assert "document.search_answered" in events

    monkeypatch.setattr(
        documents_api,
        "reason_over_context",
        AsyncMock(
            return_value={
                "provider": "openai",
                "model": "test-model",
                "answer": "The token rotates after each successful session renewal.",
                "citation_ids": [],
                "needs_more_context": False,
                "suggested_action": None,
            }
        ),
    )

    async def invoke(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        original = documents_api.reason_over_context

        async def answer_with_matching_document(request, sources, **kwargs):
            result = await original(request, sources, **kwargs)
            result["citation_ids"] = [str(sources[0].id)]
            return result

        monkeypatch.setattr(documents_api, "reason_over_context", answer_with_matching_document)
        await scenario(client, sessions)

    asyncio.run(_exercise_api(invoke))


def test_upload_rejects_over_5_mib_until_object_storage_is_configured() -> None:
    async def scenario(client: httpx.AsyncClient, _: sessionmaker) -> None:
        workspace_id = await _register(client, "large-upload-owner@example.com")
        payload = b" " * (5 * 1024 * 1024 + 1)
        response = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents/upload",
            files={"file": ("large.txt", payload, "text/plain")},
        )
        assert response.status_code == 503
        assert "Cloudflare R2" in response.json()["detail"]

    asyncio.run(_exercise_api(scenario))


def test_50_mib_upload_uses_object_storage_and_downloads_from_it(monkeypatch) -> None:
    monkeypatch.setattr(documents_api.settings, "file_storage_endpoint", "https://account.r2.test")
    monkeypatch.setattr(documents_api.settings, "file_storage_bucket", "sahakarya-test")
    monkeypatch.setattr(documents_api.settings, "file_storage_access_key_id", "test-access")
    monkeypatch.setattr(
        documents_api.settings,
        "file_storage_secret_access_key",
        SecretStr("test-secret"),
    )
    uploaded: list[tuple[str, int, str]] = []
    monkeypatch.setattr(
        documents_api,
        "upload_object",
        lambda key, content, content_type: uploaded.append((key, len(content), content_type)),
    )
    monkeypatch.setattr(documents_api, "download_object", lambda _key: b"retrieved from R2")
    monkeypatch.setattr(
        documents_api,
        "_extract_file",
        lambda _filename, _content: ("Indexed content from the uploaded text file.", "text/plain"),
    )

    async def scenario(client: httpx.AsyncClient, sessions: sessionmaker) -> None:
        workspace_id = await _register(client, "r2-upload-owner@example.com")
        base = f"/api/v1/workspaces/{workspace_id}/documents"
        file_bytes = b"x" * (50 * 1024 * 1024)
        response = await client.post(
            f"{base}/upload",
            files={"file": ("large.txt", file_bytes, "text/plain")},
        )
        assert response.status_code == 201, response.text
        document_id = response.json()["id"]
        assert uploaded and uploaded[0][1] == 50 * 1024 * 1024
        with sessions() as db:
            stored = db.get(KnowledgeDocumentFile, UUID(document_id))
            assert stored is not None
            assert stored.file_content is None
            assert stored.storage_key == uploaded[0][0]
            assert stored.file_size == 50 * 1024 * 1024
        downloaded = await client.get(f"{base}/{document_id}/file")
        assert downloaded.status_code == 200
        assert downloaded.content == b"retrieved from R2"

    asyncio.run(_exercise_api(scenario))
