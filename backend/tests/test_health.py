import asyncio

import httpx

from app.api import health
from app.main import app


async def _send_request(
    method: str, path: str, headers: dict[str, str] | None = None
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, headers=headers)


def send_request(method: str, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    return asyncio.run(_send_request(method, path, headers))


def test_liveness_returns_status_and_request_id() -> None:
    response = send_request("GET", "/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "live"
    assert response.headers["x-request-id"]


def test_valid_request_id_is_preserved() -> None:
    request_id = "4d4f64c1-5b10-46b0-bc15-aee6053ddf81"

    response = send_request("GET", "/health/live", headers={"X-Request-ID": request_id})

    assert response.headers["x-request-id"] == request_id


def test_invalid_request_id_is_replaced() -> None:
    response = send_request("GET", "/health/live", headers={"X-Request-ID": "not-a-uuid"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] != "not-a-uuid"
    assert len(response.headers["x-request-id"]) == 36


def test_versioned_api_metadata_route() -> None:
    response = send_request("GET", "/api/v1/meta")

    assert response.status_code == 200
    assert response.json()["name"] == "SAHAKARYA API"
    assert response.json()["api_version"] == "v1"


def test_readiness_returns_503_when_database_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(health, "check_database", lambda: False)

    response = send_request("GET", "/health/ready")

    assert response.status_code == 503
    assert response.json()["detail"]["dependencies"]["database"] == "unavailable"


def test_readiness_returns_200_when_database_is_available(monkeypatch) -> None:
    monkeypatch.setattr(health, "check_database", lambda: True)

    response = send_request("GET", "/health/ready")

    assert response.status_code == 200
    assert response.json()["dependencies"]["database"] == "available"
