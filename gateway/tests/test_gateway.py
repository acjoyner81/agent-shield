import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from gateway.main import app


client = TestClient(app)


@pytest.fixture
def mock_redis():
    with patch("gateway.main.r") as mock:
        yield mock


def test_unauthorized_missing_api_key():
    """Verify a missing API key returns HTTP 401 Unauthorized."""
    response = client.post("/v1/chat/completions", json={"prompt": "Test query"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_protected_route_requires_bearer_token():
    """Verify the Auth0-protected route rejects requests without a bearer token."""
    response = client.get("/api/v1/protected")

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_unauthorized_invalid_api_key():
    """Verify invalid API key returns HTTP 401 Unauthorized."""
    response = client.post(
        "/v1/chat/completions",
        headers={"X-Tenant-API-Key": "invalid_key_999"},
        json={"prompt": "Test query"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API Key"


def test_rate_limit_exceeded(mock_redis):
    """Verify a tenant exceeding its request limit receives HTTP 429."""
    mock_redis.incr.return_value = 6

    response = client.post(
        "/v1/chat/completions",
        headers={"X-Tenant-API-Key": "key_beta_456"},
        json={"prompt": "Over-limit request"},
    )

    assert response.status_code == 429
    assert "Tenant rate limit exceeded" in response.json()["detail"]


def test_semantic_cache_hit(mock_redis):
    """Verify cached responses have zero request cost."""
    mock_redis.incr.return_value = 1
    mock_redis.get.return_value = '{"text": "Cached response", "tokens_used": 0}'

    response = client.post(
        "/v1/chat/completions",
        headers={"X-Tenant-API-Key": "key_alpha_123"},
        json={"prompt": "What is Python?"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "semantic_cache"
    assert data["cost_usd"] == 0.0
    assert data["response"]["text"] == "Cached response"
