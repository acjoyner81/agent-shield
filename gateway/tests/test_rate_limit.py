"""Unit and integration tests for tenant token bucket rate limiting."""

import os
import time
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from gateway.main import app
from gateway.rate_limit import check_token_bucket, DEFAULT_TENANT_RPM

client = TestClient(app)


@pytest.fixture(autouse=True)
def enable_dev_mode():
    os.environ["DEV_MODE"] = "true"
    yield
    if "DEV_MODE" in os.environ:
        del os.environ["DEV_MODE"]


def test_token_bucket_happy_path():
    """Verify check_token_bucket decrements capacity and returns remaining tokens."""
    mock_redis = MagicMock()
    mock_redis.hgetall.return_value = {}

    allowed, remaining, limit, reset, retry_after = check_token_bucket(
        mock_redis, "tenant_alpha", capacity=60, cost=1.0, now=1000.0
    )

    assert allowed is True
    assert remaining == 59
    assert limit == 60
    assert reset == 1
    assert retry_after == 0
    mock_redis.hset.assert_called_once()


def test_token_bucket_exhaustion():
    """Verify check_token_bucket rejects requests when tokens are 0."""
    mock_redis = MagicMock()
    mock_redis.hgetall.return_value = {"tokens": "0.0", "last_updated": "1000.0"}

    allowed, remaining, limit, reset, retry_after = check_token_bucket(
        mock_redis, "tenant_alpha", capacity=60, cost=1.0, now=1000.0
    )

    assert allowed is False
    assert remaining == 0
    assert limit == 60
    assert retry_after >= 1


def test_token_bucket_refill_over_time():
    """Verify token bucket refills tokens over elapsed time."""
    mock_redis = MagicMock()
    # 0 tokens at timestamp 1000.0 (rate limit capacity 60 -> 1 token/sec refill)
    mock_redis.hgetall.return_value = {"tokens": "0.0", "last_updated": "1000.0"}

    # 10 seconds later, should have refilled ~10 tokens
    allowed, remaining, limit, reset, retry_after = check_token_bucket(
        mock_redis, "tenant_alpha", capacity=60, cost=1.0, now=1010.0
    )

    assert allowed is True
    assert remaining == 9
    assert limit == 60


def test_health_endpoint_bypasses_rate_limit():
    """Verify /health bypasses rate limiting evaluation (AC-5)."""
    with patch("gateway.rate_limit.check_token_bucket") as mock_check:
        response = client.get("/health")
        assert response.status_code == 200
        mock_check.assert_not_called()


def test_rate_limit_headers_on_success():
    """Verify valid requests return X-RateLimit headers (AC-1, AC-2)."""
    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 45, 60, 15, 0)):
        response = client.get(
            "/api/v1/protected",
            headers={
                "Authorization": "Bearer dev-mock-token",
                "DEV_MODE": "true",
            },
        )
        # Verify rate limit headers attached
        assert response.headers.get("X-RateLimit-Limit") == "60"
        assert response.headers.get("X-RateLimit-Remaining") == "45"
        assert response.headers.get("X-RateLimit-Reset") == "15"


def test_rate_limit_exhausted_returns_429():
    """Verify capacity exhaustion returns 429 with Retry-After and telemetry log (AC-3, AC-4)."""
    with patch("gateway.rate_limit.check_token_bucket", return_value=(False, 0, 60, 60, 10)), \
         patch("gateway.rate_limit.emit_rate_limit_exceeded"), \
         patch("asyncio.create_task") as mock_task:
        
        response = client.get(
            "/api/v1/protected",
            headers={
                "Authorization": "Bearer dev-mock-token",
                "DEV_MODE": "true",
            },
        )
        assert response.status_code == 429
        assert response.json()["detail"] == "Tenant rate limit exceeded"
        assert response.headers.get("Retry-After") == "10"
        assert response.headers.get("X-RateLimit-Remaining") == "0"
        
        # Verify telemetry warning was dispatched
        mock_task.assert_called_once()

def test_rate_limit_uses_stripe_redis_tier():
    """Verify rate limit capacity resolves from Redis tier setting set by Stripe webhooks."""
    mock_redis = MagicMock()
    mock_redis.get.return_value = "starter"  # Starter tier = 20 RPM
    
    with patch("gateway.rate_limit.get_redis_client", return_value=mock_redis), \
         patch("gateway.rate_limit.check_token_bucket", return_value=(True, 19, 20, 3, 0)) as mock_check:
        
        response = client.get(
            "/api/v1/protected",
            headers={
                "Authorization": "Bearer dev-mock-token",
                "DEV_MODE": "true",
            },
        )
        assert response.headers.get("X-RateLimit-Limit") == "20"
        mock_check.assert_called_once()
        assert mock_check.call_args[1]["capacity"] == 20