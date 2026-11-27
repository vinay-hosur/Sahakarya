import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.api.v1.requests import ContextSource
from app.core.config import settings
from app.models.entities import UserRequest
from app.services import reasoning
from app.services.reasoning import ReasoningUnavailable


def test_structured_reasoning_validates_source_ids_and_keeps_provider_data_bounded(
    monkeypatch,
) -> None:
    source_id = uuid4()
    model_output = {
        "answer": "The team chose a staged migration with a compatibility window.",
        "citations": [str(source_id)],
        "evidence_assessment": {
            "facts": [
                {
                    "statement": "The team chose a staged migration.",
                    "citations": [str(source_id)],
                }
            ],
            "interpretations": [
                {
                    "statement": "A staged rollout reduces migration risk.",
                    "citations": [str(source_id)],
                }
            ],
            "recommendations": [],
            "unknowns": ["The exact rollout dates are not recorded."],
            "conflicts": [
                {
                    "topic": "Compatibility window",
                    "views": [
                        {
                            "statement": "Compatibility is required for two releases.",
                            "citations": [str(source_id)],
                        },
                        {
                            "statement": "The schedule does not identify release dates.",
                            "citations": [str(source_id)],
                        },
                    ],
                }
            ],
        },
        "needs_more_context": False,
        "action_title": "Document the API migration plan",
        "action_description": "Capture the staged rollout and its compatibility window.",
    }
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "completed",
                "usage": {"input_tokens": 34, "output_tokens": 12},
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(model_output)}],
                    }
                ],
            }

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_options"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url, *, headers, json):
            captured.update({"url": url, "headers": headers, "body": json})
            return FakeResponse()

    monkeypatch.setattr(reasoning.httpx2, "AsyncClient", FakeClient)
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("test-provider-key"))
    monkeypatch.setattr(settings, "openai_model", "test-structured-model")
    request = UserRequest(prompt="What did the team decide about the API migration?")
    source = ContextSource(
        id=source_id,
        provider="github",
        tool_name="search_issues",
        title="API migration plan",
        content_text=("Use a staged migration and retain compatibility for two releases. " * 500),
        source_url="https://github.com/example/project/issues/12",
        author="vinay",
        occurred_at=None,
        relevance_score=0.7,
    )

    result = asyncio.run(reasoning.reason_over_context(request, [source]))

    assert result["provider"] == "openai"
    assert result["usage"] == {"input_tokens": 34, "output_tokens": 12, "total_tokens": 46}
    assert result["citation_ids"] == [str(source_id)]
    assert result["suggested_action"]["title"] == model_output["action_title"]
    assert (
        result["evidence_assessment"]["unknowns"] == model_output["evidence_assessment"]["unknowns"]
    )
    assert result["evidence_assessment"]["conflicts"][0]["topic"] == "Compatibility window"
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["body"]["store"] is False
    assert captured["body"]["text"]["format"]["strict"] is True
    assert captured["body"]["instructions"].find("untrusted data") >= 0
    input_value = json.loads(captured["body"]["input"])
    assert len(input_value["sources"][0]["content"]) <= reasoning.MAX_REASONING_CONTEXT_CHARACTERS
    assert captured["headers"]["Authorization"] == "Bearer test-provider-key"


def test_structured_reasoning_rejects_unknown_citation(monkeypatch) -> None:
    known_source_id = uuid4()
    unknown_source_id = uuid4()
    model_output = {
        "answer": "A plausible answer.",
        "citations": [str(known_source_id)],
        "evidence_assessment": {
            "facts": [{"statement": "Unsupported detail.", "citations": [str(unknown_source_id)]}],
            "interpretations": [],
            "recommendations": [],
            "unknowns": [],
            "conflicts": [],
        },
        "needs_more_context": False,
        "action_title": "",
        "action_description": "",
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "completed", "output_text": json.dumps(model_output)}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url, *, headers, json):
            return FakeResponse()

    monkeypatch.setattr(reasoning.httpx2, "AsyncClient", FakeClient)
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("test-provider-key"))
    monkeypatch.setattr(settings, "openai_model", "test-structured-model")
    request = UserRequest(prompt="What happened?")
    source = ContextSource(
        id=known_source_id,
        provider="github",
        tool_name="search_issues",
        title="Evidence",
        content_text="A source record.",
        source_url=None,
        author=None,
        occurred_at=None,
        relevance_score=0.5,
    )

    with pytest.raises(ReasoningUnavailable, match="outside the supplied context"):
        asyncio.run(reasoning.reason_over_context(request, [source]))


@pytest.mark.parametrize(
    ("provider", "key_field", "model_field", "url"),
    [
        (
            "anthropic",
            "anthropic_api_key",
            "anthropic_model",
            "https://api.anthropic.com/v1/messages",
        ),
        (
            "gemini",
            "gemini_api_key",
            "gemini_model",
            "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent",
        ),
        (
            "groq",
            "groq_api_key",
            "groq_model",
            "https://api.groq.com/openai/v1/responses",
        ),
        (
            "xai",
            "xai_api_key",
            "xai_model",
            "https://api.x.ai/v1/responses",
        ),
        (
            "cloudflare",
            "cloudflare_api_token",
            "cloudflare_model",
            "https://api.cloudflare.com/client/v4/accounts/account-id/ai/v1/chat/completions",
        ),
    ],
)
def test_reasoning_adapters_send_provider_specific_requests(
    monkeypatch, provider: str, key_field: str, model_field: str, url: str
) -> None:
    source_id = uuid4()
    output = {
        "answer": "The workspace uses a staged rollout.",
        "citations": [str(source_id)],
        "evidence_assessment": {
            "facts": [
                {
                    "statement": "The workspace uses a staged rollout.",
                    "citations": [str(source_id)],
                }
            ],
            "interpretations": [],
            "recommendations": [],
            "unknowns": [],
            "conflicts": [],
        },
        "needs_more_context": False,
        "action_title": "",
        "action_description": "",
    }
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            text = json.dumps(output)
            if provider == "cloudflare":
                return {
                    "choices": [{"message": {"content": text}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
                }
            if provider in {"groq", "xai"}:
                return {
                    "status": "completed",
                    "usage": {"input_tokens": 20, "output_tokens": 8},
                    "output_text": text,
                }
            if provider == "anthropic":
                return {
                    "content": [{"type": "text", "text": text}],
                    "usage": {"input_tokens": 20, "output_tokens": 8},
                }
            return {
                "candidates": [{"content": {"parts": [{"text": text}], "role": "model"}}],
                "usageMetadata": {
                    "promptTokenCount": 20,
                    "candidatesTokenCount": 8,
                    "totalTokenCount": 28,
                },
            }

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_options"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, request_url, *, headers, json):
            captured.update({"url": request_url, "headers": headers, "body": json})
            return FakeResponse()

    monkeypatch.setattr(reasoning.httpx2, "AsyncClient", FakeClient)
    monkeypatch.setattr(settings, key_field, SecretStr("provider-secret"))
    monkeypatch.setattr(settings, model_field, "test-model")
    if provider == "cloudflare":
        monkeypatch.setattr(settings, "cloudflare_account_id", "account-id")
    source = SimpleNamespace(
        id=source_id,
        provider="native_document",
        title="Rollout notes",
        content_text="Roll out the migration gradually and keep a compatibility window.",
        source_url=None,
    )

    result = asyncio.run(
        reasoning.reason_over_context(
            SimpleNamespace(prompt="How should we roll out?"), [source], provider=provider
        )
    )

    assert result["provider"] == provider
    assert result["model"] == "test-model"
    assert result["citation_ids"] == [str(source_id)]
    assert result["usage"]["total_tokens"] == 28
    assert captured["url"] == url
    if provider == "cloudflare":
        assert captured["headers"]["Authorization"] == "Bearer provider-secret"
        assert captured["body"]["model"] == "test-model"
        assert captured["body"]["response_format"]["json_schema"]["strict"] is True
    elif provider in {"groq", "xai"}:
        assert captured["headers"]["Authorization"] == "Bearer provider-secret"
        assert "store" not in captured["body"]
        assert captured["body"]["text"]["format"]["strict"] is True
    elif provider == "anthropic":
        assert captured["headers"]["x-api-key"] == "provider-secret"
        assert captured["body"]["messages"][0]["role"] == "user"
    else:
        assert captured["headers"]["x-goog-api-key"] == "provider-secret"
        assert captured["body"]["generationConfig"]["responseMimeType"] == "application/json"


def test_native_task_proposals_use_strict_schema_and_validate_evidence(monkeypatch) -> None:
    source_id = uuid4()
    output = {
        "summary": "The migration notes support one implementation task.",
        "needs_more_context": False,
        "tasks": [
            {
                "title": "Rotate refresh tokens",
                "description": "Rotate tokens after each successful refresh.",
                "priority": "high",
                "labels": ["auth", "security"],
                "citations": [str(source_id)],
            }
        ],
    }
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "completed",
                "usage": {"input_tokens": 34, "output_tokens": 12},
                "output_text": json.dumps(output),
            }

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url, *, headers, json):
            captured.update({"url": url, "headers": headers, "body": json})
            return FakeResponse()

    monkeypatch.setattr(reasoning.httpx2, "AsyncClient", FakeClient)
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("test-provider-key"))
    monkeypatch.setattr(settings, "openai_model", "test-structured-model")
    source = SimpleNamespace(
        id=source_id,
        provider="native_document",
        title="Refresh token policy",
        content_text="Rotate refresh tokens and reject reuse after a successful refresh.",
        source_url=None,
    )

    result = asyncio.run(
        reasoning.propose_native_tasks(
            "Break the token policy into an implementation task.", [source], provider="openai"
        )
    )

    assert result["provider"] == "openai"
    assert result["tasks"][0]["citations"] == [str(source_id)]
    assert result["tasks"][0]["labels"] == ["auth", "security"]
    assert result["usage"]["total_tokens"] == 46
    assert captured["body"]["text"]["format"]["name"] == "sahakarya_native_task_proposals"
    assert captured["body"]["text"]["format"]["strict"] is True
    assert "editable drafts only" in captured["body"]["instructions"]

    output["tasks"][0]["citations"] = [str(uuid4())]
    with pytest.raises(ReasoningUnavailable, match="outside the supplied context"):
        asyncio.run(
            reasoning.propose_native_tasks(
                "Break the token policy into an implementation task.",
                [source],
                provider="openai",
            )
        )
