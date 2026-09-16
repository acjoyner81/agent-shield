import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from gateway.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def enable_dev_mode():
    os.environ["DEV_MODE"] = "true"
    yield
    if "DEV_MODE" in os.environ:
        del os.environ["DEV_MODE"]


@pytest.fixture
def mock_redis():
    with patch("gateway.main.r") as mock:
        yield mock


def test_unauthorized_missing_api_key():
    """Verify a missing bearer token returns HTTP 401 Unauthorized."""
    # Temporarily disable DEV_MODE to test 401
    del os.environ["DEV_MODE"]
    response = client.post("/v1/chat/completions", json={"prompt": "Test query"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_protected_route_requires_bearer_token():
    """Verify the Auth0-protected route rejects requests without a bearer token."""
    del os.environ["DEV_MODE"]
    response = client.get("/api/v1/protected")

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_rate_limit_exceeded(mock_redis):
    """Verify a tenant exceeding its request limit receives HTTP 429."""
    with patch("gateway.rate_limit.check_token_bucket", return_value=(False, 0, 5, 60, 12)):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer dev-mock-token"},
            json={"prompt": "Over-limit request"},
        )

        assert response.status_code == 429
        assert "Tenant rate limit exceeded" in response.json()["detail"]


def test_semantic_cache_hit(mock_redis):
    """Verify cached responses have zero request cost."""
    mock_redis.get.return_value = '{"text": "Cached response", "tokens_used": 0}'

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer dev-mock-token"},
            json={"prompt": "What is Python?"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "semantic_cache"
        assert data["cost_usd"] == 0.0
        assert data["response"]["text"] == "Cached response"
