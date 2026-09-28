import os
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

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


def test_health_endpoint_returns_ok():
    """Verify /health returns status ok without requiring auth."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "gateway"


def test_health_bypasses_rate_limit(mock_redis):
    """Verify /health bypasses rate limiting evaluation."""
    with patch("gateway.rate_limit.check_token_bucket") as mock_check:
        response = client.get("/health")
        assert response.status_code == 200
        mock_check.assert_not_called()


def test_telemetry_logs_get_returns_list(mock_redis):
    """Verify /v1/telemetry/logs returns tenant-scoped telemetry history."""
    mock_redis.lrange.return_value = [
        '{"tenant_id": "tenant_alpha", "level": "INFO", "message": "test"}'
    ]
    response = client.get(
        "/v1/telemetry/logs",
        headers={"Authorization": "Bearer dev-mock-token"},
    )
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_telemetry_logs_get_requires_auth(mock_redis):
    """Verify /v1/telemetry/logs does not leak data anonymously."""
    response = client.get("/v1/telemetry/logs")
    assert response.status_code == 401
    assert mock_redis.lrange.called is False


def test_telemetry_logs_post_accepts_payload(mock_redis):
    """Verify /v1/telemetry/logs POST accepts and processes telemetry payload."""
    mock_redis.lpush = MagicMock()
    mock_redis.ltrim = MagicMock()
    with patch("gateway.main.requests.post") as mock_post:
        mock_post.return_value = MagicMock()
        mock_post.return_value.raise_for_status = lambda: None
        response = client.post(
            "/v1/telemetry/logs",
            json={"tenant_id": "tenant_alpha", "level": "INFO", "message": "Test telemetry"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_telemetry_logs_post_splunk_failure(mock_redis):
    """Verify /v1/telemetry/logs POST handles Splunk failure gracefully."""
    mock_redis.lpush = MagicMock()
    mock_redis.ltrim = MagicMock()
    with patch("gateway.main.requests.post", side_effect=Exception("Connection refused")):
        response = client.post(
            "/v1/telemetry/logs",
            json={"tenant_id": "tenant_alpha", "level": "INFO", "message": "Test telemetry"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"





def test_chat_completions_budget_exceeded(fake_redis):
    """Verify /v1/chat/completions returns 402 when daily budget exceeded."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    fake_redis.set(f"budget:tenant_alpha:{today}", "100.0")
    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer dev-mock-token"},
            json={"prompt": "Test query"},
        )
    assert response.status_code == 402
    assert "daily budget exceeded" in response.json()["detail"]





def test_chat_completions_rate_limit(fake_redis):
    """Verify /v1/chat/completions enforces rate limit."""
    with patch("gateway.rate_limit.check_token_bucket", return_value=(False, 0, 60, 60, 10)), \
         patch("gateway.rate_limit.emit_rate_limit_exceeded"), \
         patch("asyncio.create_task"):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer dev-mock-token"},
            json={"prompt": "Over-limit request"},
        )
    assert response.status_code == 429
    assert "Tenant rate limit exceeded" in response.json()["detail"]
