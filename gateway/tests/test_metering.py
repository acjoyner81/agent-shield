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
    def test_process_token_event_happy_path(self, fake_redis):
        """AC-1, AC-3: Valid token usage event updates daily and monthly roll-up keys."""
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

        success = process_token_event(event, r_client=fake_redis)
        assert success is True

        daily = fake_redis.hgetall("usage:daily:tenant_meter_1:2026-09-23:gpt-4o")
        assert daily == {
            "input_tokens": "120",
            "output_tokens": "40",
            "total_tokens": "160",
            "request_count": "1",
        }
        assert fake_redis.sismember("usage:models:tenant_meter_1:2026-09-23", "gpt-4o")
        assert fake_redis.hgetall("billing:usage:tenant_meter_1:2026-09") == {
            "total_tokens": "160",
            "request_count": "1",
        }
        assert fake_redis.sismember("usage:processed_events", "evt_test_123")

    def test_process_token_event_deduplication(self, fake_redis):
        """AC-2: Duplicate event_id is skipped and does not re-increment roll-up."""
        event = {
            "type": "agentshield.token.usage",
            "event_id": "evt_duplicate_999",
            "tenant_id": "tenant_meter_1",
            "timestamp": "2026-09-23T10:00:00Z",
            "data": {"tokens": {"input": 100, "output": 50, "total": 150, "model": "gpt-4o"}}
        }

        assert process_token_event(event, r_client=fake_redis) is True
        assert process_token_event(event, r_client=fake_redis) is True

        daily = fake_redis.hgetall("usage:daily:tenant_meter_1:2026-09-23:gpt-4o")
        assert daily["total_tokens"] == "150"
        assert daily["request_count"] == "1", "a duplicate must not be counted twice"

    def test_process_token_event_malformed_routes_to_dlq(self, fake_redis):
        """AC-6: Malformed/unparseable event is forwarded to DLQ without raising."""
        malformed_event = "not a valid json object"

        success = process_token_event(malformed_event, r_client=fake_redis)
        assert success is False
        assert fake_redis.lrange("telemetry:dlq", 0, -1) == [malformed_event]

    def test_dlq_redrive_replays_an_event_the_first_attempt_rejected(self, fake_redis):
        """A rolled up event that failed must redrive, not die as a false duplicate.

        The claim and the roll-up are one atomic step, so a rejected attempt
        leaves no ledger entry to make the redrive look like a duplicate.
        """
        event = {
            "type": "agentshield.token.usage",
            "event_id": "evt_redrive_1",
            "tenant_id": "tenant_meter_1",
            "timestamp": "2026-09-23T10:00:00Z",
            "data": {"tokens": {"input": 100, "output": 50, "total": 150, "model": "gpt-4o"}}
        }
        daily_key = "usage:daily:tenant_meter_1:2026-09-23:gpt-4o"

        # A key of the wrong type makes the roll-up fail part way through
        fake_redis.set(daily_key, "WRONGTYPE")

        assert process_token_event(event, r_client=fake_redis) is False
        assert fake_redis.lrange("telemetry:dlq", 0, -1) == [json.dumps(event)]
        assert not fake_redis.sismember("usage:processed_events", "evt_redrive_1"), (
            "a failed event must not stay claimed, or the redrive is dropped as a duplicate"
        )

        # The fault clears, the operator redrives the DLQ entry
        fake_redis.delete(daily_key)

        assert process_token_event(event, r_client=fake_redis) is True
        assert fake_redis.hget(daily_key, "total_tokens") == "150"
        assert fake_redis.hget(daily_key, "request_count") == "1"

    def test_a_rejected_event_does_not_leave_a_partial_roll_up(self, fake_redis):
        """The roll-up is all or nothing, so a hash is never left half updated."""
        event = {
            "type": "agentshield.token.usage",
            "event_id": "evt_partial_1",
            "tenant_id": "tenant_meter_1",
            "timestamp": "2026-09-23T10:00:00Z",
            "data": {"tokens": {"input": 100, "output": 50, "total": 150, "model": "gpt-4o"}}
        }
        billing_key = "billing:usage:tenant_meter_1:2026-09"
        fake_redis.set(billing_key, "WRONGTYPE")

        assert process_token_event(event, r_client=fake_redis) is False
        assert fake_redis.hgetall("usage:daily:tenant_meter_1:2026-09-23:gpt-4o") == {}, (
            "the daily hash must not be written when the event as a whole is rejected"
        )
        assert fake_redis.smembers("usage:models:tenant_meter_1:2026-09-23") == set()

    def test_process_meta_event_rolls_up_and_deduplicates(self, fake_redis):
        """AC-2: A dashboard counter event is counted once, then recognised as a duplicate."""
        from gateway.metering import process_meta_event

        event = {
            "type": "agentshield.security.authz_failure",
            "event_id": "evt_meta_1",
            "tenant_id": "tenant_meter_1",
            "timestamp": "2026-09-23T10:00:00Z",
        }
        meta_key = "usage:daily:tenant_meter_1:2026-09-23:__meta__"

        assert process_meta_event(event, r_client=fake_redis) is True
        assert process_meta_event(event, r_client=fake_redis) is True

        assert fake_redis.hgetall(meta_key) == {"failed_requests": "1"}

    def test_meta_event_dlq_redrive_replays_a_rejected_event(self, fake_redis):
        """The same redrive guarantee holds for the dashboard counters."""
        from gateway.metering import process_meta_event

        event = {
            "type": "agentshield.security.authz_failure",
            "event_id": "evt_meta_redrive",
            "tenant_id": "tenant_meter_1",
            "timestamp": "2026-09-23T10:00:00Z",
        }
        meta_key = "usage:daily:tenant_meter_1:2026-09-23:__meta__"
        fake_redis.set(meta_key, "WRONGTYPE")

        assert process_meta_event(event, r_client=fake_redis) is False
        assert not fake_redis.sismember("usage:processed_events", "evt_meta_redrive")

        fake_redis.delete(meta_key)

        assert process_meta_event(event, r_client=fake_redis) is True
        assert fake_redis.hget(meta_key, "failed_requests") == "1"

    def test_get_tenant_usage_summary_aggregates(self, fake_redis):
        """AC-5: Querying usage summary aggregates across models for the period."""
        fake_redis.hset("usage:daily:tenant_alpha:2026-09-20:gpt-4o", mapping={
            "input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200, "request_count": 5,
        })
        fake_redis.hset("usage:daily:tenant_alpha:2026-09-21:claude-3-5-sonnet", mapping={
            "input_tokens": 500, "output_tokens": 100, "total_tokens": 600, "request_count": 2,
        })

        summary = get_tenant_usage_summary(
            tenant_id="tenant_alpha",
            start_date="2026-09-01",
            end_date="2026-09-30",
            r_client=fake_redis,
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
