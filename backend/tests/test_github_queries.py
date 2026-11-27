import asyncio
import base64
from types import SimpleNamespace

from app.services import context_retrieval


def test_github_repository_count_uses_last_page_from_rest_pagination(monkeypatch) -> None:
    response_payload = [{"full_name": "limitless/sahakarya"}]
    calls: list[dict] = []

    class FakeResponse:
        headers = {"link": '<https://api.github.com/user/repos?per_page=1&page=46>; rel="last"'}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list:
            return response_payload

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.headers = kwargs["headers"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get(self, url: str, *, params: dict) -> FakeResponse:
            calls.append({"url": url, "params": params, "headers": self.headers})
            return FakeResponse()

    monkeypatch.setattr(context_retrieval.httpx2, "AsyncClient", FakeClient)
    integration = SimpleNamespace(
        provider="github", config_json={"endpoint": "https://api.githubcopilot.com/mcp/"}
    )

    async def run():
        return await context_retrieval.retrieve_from_integration(
            integration, "test-token", "How many repositories do I have?"
        )

    results = asyncio.run(run())
    assert len(results) == 1
    assert "46 repositories" in results[0].content_text
    assert results[0].metadata["repository_count"] == 46
    assert results[0].metadata["query_kind"] == "count"
    assert calls[0]["url"] == context_retrieval.GITHUB_REPOSITORIES_URL
    assert calls[0]["params"]["per_page"] == 1


def test_github_repository_list_returns_recent_names_and_is_narrowly_triggered(monkeypatch) -> None:
    response_payload = {
        "data": [
            {
                "full_name": "limitless/sahakarya",
                "description": "Workspace app",
                "html_url": "https://github.com/limitless/sahakarya",
                "private": True,
                "language": "Python",
            },
            {
                "full_name": "limitless/site",
                "description": "Public site",
                "html_url": "https://github.com/limitless/site",
                "private": False,
                "language": "TypeScript",
            },
        ],
    }

    class FakeResponse:
        headers = {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list:
            return response_payload["data"]

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get(self, *_args, **_kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(context_retrieval.httpx2, "AsyncClient", FakeClient)
    integration = SimpleNamespace(
        provider="github", config_json={"endpoint": "https://api.githubcopilot.com/mcp/"}
    )

    async def run():
        return await context_retrieval.retrieve_from_integration(
            integration, "test-token", "List my repositories"
        )

    results = asyncio.run(run())
    assert "limitless/sahakarya" in results[0].content_text
    assert "limitless/site" in results[0].content_text
    assert "Python" in results[0].content_text
    assert context_retrieval.github_repository_intent("What is there in Limitless repo?") is None


def test_github_repository_question_retrieves_readme_tree_and_source(monkeypatch) -> None:
    calls: list[str] = []
    readme = "SAHAKARYA joins project context, team messages, decisions, and AI assistance."

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def __init__(self, payload, text: str | None = None) -> None:
            self.payload = payload
            self.text = text or ""

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get(self, url: str, *, params: dict | None = None) -> FakeResponse:
            calls.append(url)
            if url == context_retrieval.GITHUB_REPOSITORIES_URL:
                return FakeResponse(
                    [
                        {
                            "full_name": "limitless/sahakarya",
                            "html_url": "https://github.com/limitless/sahakarya",
                            "description": "Project collaboration and intelligence",
                            "default_branch": "main",
                            "language": "Python",
                            "updated_at": "2026-11-26T12:00:00Z",
                        }
                    ]
                )
            if url.endswith("/readme"):
                return FakeResponse(
                    {
                        "encoding": "base64",
                        "content": base64.b64encode(readme.encode()).decode(),
                        "html_url": "https://github.com/limitless/sahakarya/blob/main/README.md",
                    }
                )
            if "/git/trees/main" in url:
                return FakeResponse(
                    {
                        "truncated": False,
                        "tree": [
                            {"type": "blob", "path": "README.md"},
                            {"type": "blob", "path": "package.json"},
                            {"type": "blob", "path": "backend/app/main.py"},
                        ],
                    }
                )
            if url.endswith("/contents/package.json"):
                return FakeResponse(None, '{"name":"sahakarya","scripts":{"build":"vite build"}}')
            if url.endswith("/contents/backend/app/main.py"):
                return FakeResponse(None, "from fastapi import FastAPI\napp = FastAPI()")
            raise AssertionError(f"Unexpected GitHub API request: {url} {params}")

    monkeypatch.setattr(context_retrieval.httpx2, "AsyncClient", FakeClient)
    integration = SimpleNamespace(
        provider="github", config_json={"endpoint": "https://api.githubcopilot.com/mcp/"}
    )

    async def run():
        return await context_retrieval.retrieve_from_integration(
            integration, "test-token", "What does the Sahakarya repository do?"
        )

    results = asyncio.run(run())
    by_type = {item.metadata.get("content_type"): item for item in results}
    assert "metadata" in by_type
    assert by_type["readme"].content_text == readme
    assert by_type["tree"].content_text.splitlines() == [
        "README.md",
        "package.json",
        "backend/app/main.py",
    ]
    assert any(item.title.endswith("package.json") for item in results)
    assert any(item.title.endswith("backend/app/main.py") for item in results)
    assert all(
        item.source_url and item.source_url.startswith("https://github.com/") for item in results
    )
    assert "https://api.github.com/repos/limitless/sahakarya/readme" in calls


def test_public_github_repository_uses_anonymous_reads_when_saved_token_is_restricted(
    monkeypatch,
) -> None:
    calls: list[tuple[str, str | None]] = []
    readme = "SAHAKARYA is a collaboration workspace for teams."

    class FakeResponse:
        def __init__(self, status_code: int, payload=None) -> None:
            self.status_code = status_code
            self.payload = payload
            self.headers = {}
            self.text = ""

        @property
        def is_success(self) -> bool:
            return 200 <= self.status_code < 300

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise AssertionError(f"Unexpected HTTP {self.status_code}")

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, *, headers: dict, **_kwargs) -> None:
            self.headers = headers

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get(self, url: str, *, params=None, headers=None) -> FakeResponse:
            request_headers = {**self.headers, **(headers or {})}
            authorization = request_headers.get("Authorization")
            calls.append((url, authorization))
            if url == "https://api.github.com/repos/vinay-hosur/Sahakarya":
                if authorization:
                    return FakeResponse(404)
                return FakeResponse(
                    200,
                    {
                        "full_name": "vinay-hosur/Sahakarya",
                        "html_url": "https://github.com/vinay-hosur/Sahakarya",
                        "description": "Team collaboration workspace",
                        "default_branch": "main",
                        "language": "Python",
                        "private": False,
                    },
                )
            if url.endswith("/readme"):
                return FakeResponse(
                    200,
                    {
                        "encoding": "base64",
                        "content": base64.b64encode(readme.encode()).decode(),
                        "html_url": "https://github.com/vinay-hosur/Sahakarya/blob/main/README.md",
                    },
                )
            if "/git/trees/main" in url:
                return FakeResponse(200, {"truncated": False, "tree": []})
            raise AssertionError(f"Unexpected GitHub API request: {url} {params}")

    monkeypatch.setattr(context_retrieval.httpx2, "AsyncClient", FakeClient)
    results = asyncio.run(
        context_retrieval.retrieve_github_repository_context(
            "restricted-token",
            "Read the README for vinay-hosur/Sahakarya and summarize its purpose.",
        )
    )

    assert any(item.metadata.get("content_type") == "readme" for item in results)
    assert (
        next(item for item in results if item.metadata.get("content_type") == "readme").content_text
        == readme
    )
    assert calls[0] == (
        "https://api.github.com/repos/vinay-hosur/Sahakarya",
        "Bearer restricted-token",
    )
    assert calls[1] == ("https://api.github.com/repos/vinay-hosur/Sahakarya", None)
    assert all(authorization is None for _, authorization in calls[2:])
