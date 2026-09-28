"""Unit and integration tests for Usage Metering Engine (Spec 0009)."""

import json
import os
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from gateway.main import app
from gateway.metering import process_token_event, get_tenant_usage_summary


client = TestClient(app)

CLIENT_HEADERS = {"Authorization": "Bearer dev-mock-token"}


@pytest.fixture(autouse=True)
def enable_dev_mode():
    os.environ["DEV_MODE"] = "true"
    yield
    os.environ.pop("DEV_MODE", None)


class TestUsageMeteringEngine:
    def test_process_token_event_happy_path(self):
        """AC-1, AC-3: Valid token usage event updates daily and monthly roll-up keys."""
        mock_redis = MagicMock()
        mock_redis.sadd.return_value = 1  # Not duplicate

        event = {
            "specversion": "1.0",
            "type": "agentshield.token.usage",
            "event_id": "evt_test_123",
            "tenant_id": "tenant_meter_1",
            "timestamp": "2026-09-23T10:00:00Z",
            "data": {
                "tokens": {
                    "input": 120,
                    "output": 40,
                    "total": 160,
                    "model": "gpt-4o"
                }
            }
        }

        success = process_token_event(event, r_client=mock_redis)
        assert success is True
        mock_redis.sadd.assert_any_call("usage:processed_events", "evt_test_123")
        mock_redis.hincrby.assert_any_call("usage:daily:tenant_meter_1:2026-09-23:gpt-4o", "input_tokens", 120)
        mock_redis.hincrby.assert_any_call("usage:daily:tenant_meter_1:2026-09-23:gpt-4o", "output_tokens", 40)
        mock_redis.hincrby.assert_any_call("usage:daily:tenant_meter_1:2026-09-23:gpt-4o", "total_tokens", 160)
        mock_redis.hincrby.assert_any_call("usage:daily:tenant_meter_1:2026-09-23:gpt-4o", "request_count", 1)

    def test_process_token_event_deduplication(self):
        """AC-2: Duplicate event_id is skipped and does not re-increment roll-up."""
        mock_redis = MagicMock()
        mock_redis.sadd.return_value = 0  # Already in processed set

        event = {
            "type": "agentshield.token.usage",
            "event_id": "evt_duplicate_999",
            "tenant_id": "tenant_meter_1",
            "timestamp": "2026-09-23T10:00:00Z",
            "data": {"tokens": {"input": 100, "output": 50, "total": 150, "model": "gpt-4o"}}
        }

        success = process_token_event(event, r_client=mock_redis)
        assert success is True
        mock_redis.hincrby.assert_not_called()

    def test_process_token_event_malformed_routes_to_dlq(self):
        """AC-6: Malformed/unparseable event is forwarded to DLQ without raising."""
        mock_redis = MagicMock()
        malformed_event = "not a valid json object"

        success = process_token_event(malformed_event, r_client=mock_redis)
        assert success is False
        mock_redis.lpush.assert_called_once_with("telemetry:dlq", malformed_event)

    def test_get_tenant_usage_summary_aggregates(self):
        """AC-5: Querying usage summary aggregates across models for the period."""
        mock_redis = MagicMock()
        mock_redis.scan.side_effect = [
            (0, ["usage:daily:tenant_alpha:2026-09-20:gpt-4o", "usage:daily:tenant_alpha:2026-09-21:claude-3-5-sonnet"])
        ]

        def mock_hgetall(key):
            if "gpt-4o" in key:
                return {"input_tokens": "1000", "output_tokens": "200", "total_tokens": "1200", "request_count": "5"}
            return {"input_tokens": "500", "output_tokens": "100", "total_tokens": "600", "request_count": "2"}

        mock_redis.hgetall.side_effect = mock_hgetall

        summary = get_tenant_usage_summary(
            tenant_id="tenant_alpha",
            start_date="2026-09-01",
            end_date="2026-09-30",
            r_client=mock_redis,
        )

        assert summary.tenant_id == "tenant_alpha"
        assert summary.totals.total_tokens == 1800
        assert summary.totals.total_requests == 7
        assert len(summary.by_model) == 2

    def test_usage_summary_endpoint(self):
        """AC-5: Endpoint /v1/usage/summary returns structured response for verified tenant."""
        with patch("gateway.metering.get_redis_client") as mock_get_r:
            mock_redis = MagicMock()
            mock_redis.scan.return_value = (0, [])
            mock_get_r.return_value = mock_redis

            response = client.get("/v1/usage/summary", headers=CLIENT_HEADERS)
            assert response.status_code == 200
            data = response.json()
            assert "tenant_id" in data
            assert "totals" in data
            assert "by_model" in data
