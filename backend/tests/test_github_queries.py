import asyncio
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
