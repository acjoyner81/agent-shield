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
        assert fake_redis.sismember(
            "usage:processed_events", "token:tenant_meter_1:evt_test_123"
        )

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
        assert not fake_redis.sismember(
            "usage:processed_events", "token:tenant_meter_1:evt_redrive_1"
        ), (
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
        assert not fake_redis.sismember(
            "usage:processed_events", "meta:tenant_meter_1:evt_meta_redrive"
        )

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


class TestRollUpRejectsBeforeItClaims:
    """The type check has to cover every key the script writes.

    The wrong-type tests elsewhere in this file only ever stored a string, which
    the old check rejected for the wrong reason: it accepted any key that was a
    set or a hash, so a set at a hash key passed, and the model key was not
    checked at all. Either way the failure landed after SADD on the ledger, so
    the event stayed claimed and the redrive that the DLQ entry invited came
    back as a duplicate. Billing never moved.
    """

    EVENT = {
        "type": "agentshield.token.usage",
        "event_id": "evt_wrongtype",
        "tenant_id": "tenant_meter_1",
        "timestamp": "2026-09-23T10:00:00Z",
        "data": {"tokens": {"input": 100, "output": 50, "total": 150, "model": "gpt-4o"}},
    }
    LEDGER = "usage:processed_events"
    DAILY = "usage:daily:tenant_meter_1:2026-09-23:gpt-4o"
    MODELS = "usage:models:tenant_meter_1:2026-09-23"
    BILLING = "billing:usage:tenant_meter_1:2026-09"

    def _plant(self, fake_redis, key, kind):
        """Put a key of the given Redis type where the roll-up expects its own."""
        fake_redis.delete(key)
        if kind == "set":
            fake_redis.sadd(key, "leftover")
        elif kind == "hash":
            fake_redis.hset(key, "leftover", "1")
        elif kind == "list":
            fake_redis.rpush(key, "leftover")
        else:
            fake_redis.set(key, "leftover")
        return {key: kind}

    def _read(self, fake_redis, key, kind):
        """Read a key back with the accessor that matches its type."""
        if kind == "set":
            return fake_redis.smembers(key)
        if kind == "hash":
            return fake_redis.hgetall(key)
        if kind == "list":
            return fake_redis.lrange(key, 0, -1)
        return fake_redis.get(key)

    def _planted_state(self, key, kind):
        return {
            "set": {"leftover"},
            "hash": {"leftover": "1"},
            "list": ["leftover"],
        }.get(kind, "leftover")

    def _assert_clean_rejection(self, fake_redis, planted):
        """Nothing applied and nothing claimed, so a redrive can still succeed.

        Every key the script writes is checked: a planted one must still hold
        exactly what the test put there, and an untouched one must be empty.
        """
        assert not fake_redis.sismember(self.LEDGER, "token:tenant_meter_1:evt_wrongtype")
        for key in (self.DAILY, self.MODELS, self.BILLING):
            kind = planted.get(key)
            if kind is not None:
                assert self._read(fake_redis, key, kind) == self._planted_state(key, kind)
            elif key == self.MODELS:
                assert fake_redis.smembers(key) == set()
            else:
                assert fake_redis.hgetall(key) == {}

    def test_a_string_at_the_model_key_is_rejected(self, fake_redis):
        """The model key was never type checked, so this failed after the claim."""
        planted = self._plant(fake_redis, self.MODELS, "string")

        assert process_token_event(self.EVENT, r_client=fake_redis) is False

        self._assert_clean_rejection(fake_redis, planted)

    def test_a_hash_at_the_model_key_is_rejected(self, fake_redis):
        """A hash is not a set, even though the old union check accepted both."""
        planted = self._plant(fake_redis, self.MODELS, "hash")

        assert process_token_event(self.EVENT, r_client=fake_redis) is False

        self._assert_clean_rejection(fake_redis, planted)

    def test_a_list_at_the_model_key_is_rejected(self, fake_redis):
        """A list is what lpush leaves behind, and DLQ handling uses lpush."""
        planted = self._plant(fake_redis, self.MODELS, "list")

        assert process_token_event(self.EVENT, r_client=fake_redis) is False

        self._assert_clean_rejection(fake_redis, planted)

    def test_a_set_at_the_daily_key_is_rejected(self, fake_redis):
        """The union check let a set through at a key that gets HINCRBY."""
        planted = self._plant(fake_redis, self.DAILY, "set")

        assert process_token_event(self.EVENT, r_client=fake_redis) is False

        self._assert_clean_rejection(fake_redis, planted)

    def test_a_set_at_the_billing_key_is_rejected(self, fake_redis):
        planted = self._plant(fake_redis, self.BILLING, "set")

        assert process_token_event(self.EVENT, r_client=fake_redis) is False

        self._assert_clean_rejection(fake_redis, planted)

    def test_a_rejected_event_redrives_to_the_full_total(self, fake_redis):
        """The whole point: the redrive must land, not report a duplicate."""
        fake_redis.delete(self.MODELS)
        fake_redis.set(self.MODELS, "leftover")

        assert process_token_event(self.EVENT, r_client=fake_redis) is False
        assert len(fake_redis.lrange("telemetry:dlq", 0, -1)) == 1

        fake_redis.delete(self.MODELS)
        assert process_token_event(self.EVENT, r_client=fake_redis) is True

        assert fake_redis.hget(self.BILLING, "total_tokens") == "150"
        assert fake_redis.sismember(self.LEDGER, "token:tenant_meter_1:evt_wrongtype")

    def test_a_meta_key_holding_a_set_is_rejected(self, fake_redis):
        """The same union mistake in the second script."""
        from gateway.metering import process_meta_event

        meta_event = {
            "type": "agentshield.telemetry.request.completed",
            "event_id": "evt_meta_wrongtype",
            "tenant_id": "tenant_meter_1",
            "timestamp": "2026-09-23T10:00:00Z",
            "data": {"eval_passed": True},
        }
        meta_key = "usage:daily:tenant_meter_1:2026-09-23:__meta__"
        fake_redis.sadd(meta_key, "leftover")

        assert process_meta_event(meta_event, r_client=fake_redis) is False
        assert not fake_redis.sismember(self.LEDGER, "meta:tenant_meter_1:evt_meta_wrongtype")


class TestDedupClaimIsNamespaced:
    """One ledger, so the claim has to name whose event it is."""

    EVENT = {
        "type": "agentshield.token.usage",
        "event_id": "shared-id",
        "timestamp": "2026-09-23T10:00:00Z",
        "data": {"tokens": {"input": 100, "output": 50, "total": 150, "model": "gpt-4o"}},
    }

    def test_two_tenants_emitting_the_same_id_both_get_counted(self, fake_redis):
        """CloudEvent ids are unique per source, so this is routine, not exotic.

        Claiming on the bare id made the second tenant a duplicate of the first,
        so its usage was never recorded and nothing said so.
        """
        for tenant in ("tenant_meter_1", "tenant_meter_2"):
            event = dict(self.EVENT, tenant_id=tenant)

            assert process_token_event(event, r_client=fake_redis) is True

            billing = f"billing:usage:{tenant}:2026-09"
            assert fake_redis.hget(billing, "total_tokens") == "150"
            assert fake_redis.hget(billing, "request_count") == "1"

    def test_a_tenant_still_deduplicates_its_own_repeat(self, fake_redis):
        event = dict(self.EVENT, tenant_id="tenant_meter_1")

        assert process_token_event(event, r_client=fake_redis) is True
        assert process_token_event(event, r_client=fake_redis) is True

        assert fake_redis.hget("billing:usage:tenant_meter_1:2026-09", "request_count") == "1"


class TestUntrustedKeyComponentsAreRejected:
    """Tenant and model ids are pasted into Redis key names and globs."""

    def _event(self, **overrides):
        base = {
            "type": "agentshield.token.usage",
            "event_id": "evt_unsafe",
            "tenant_id": "tenant_meter_1",
            "timestamp": "2026-09-23T10:00:00Z",
            "data": {"tokens": {"input": 1, "output": 1, "total": 2, "model": "gpt-4o"}},
        }
        base.update(overrides)
        return base

    def test_a_glob_in_the_tenant_id_is_rejected(self, fake_redis):
        """`usage:daily:*:*` matches every tenant, so the scan would read them all."""
        assert process_token_event(self._event(tenant_id="*"), r_client=fake_redis) is False
        assert len(fake_redis.lrange("telemetry:dlq", 0, -1)) == 1

    def test_a_colon_in_the_tenant_id_is_rejected(self, fake_redis):
        """A colon would let the id forge its own key layout."""
        assert (
            process_token_event(self._event(tenant_id="tenant_a:2026-09-23"), r_client=fake_redis)
            is False
        )

    def test_a_colon_in_the_model_name_is_rejected(self, fake_redis):
        """Two models sharing a prefix would otherwise merge into one key."""
        event = self._event()
        event["data"] = {"tokens": {"input": 1, "output": 1, "total": 2, "model": "gpt-4o:x"}}

        assert process_token_event(event, r_client=fake_redis) is False

    def test_a_clean_id_still_works(self, fake_redis):
        assert process_token_event(self._event(), r_client=fake_redis) is True


class TestImplausibleTokenCountsAreRejected:
    def _event(self, **tokens):
        return {
            "type": "agentshield.token.usage",
            "event_id": "evt_counts",
            "tenant_id": "tenant_meter_1",
            "timestamp": "2026-09-23T10:00:00Z",
            "data": {"tokens": dict({"model": "gpt-4o"}, **tokens)},
        }

    def test_a_negative_count_is_rejected_not_clamped(self, fake_redis):
        """HINCRBY accepts a negative happily and the totals walk backwards."""
        assert (
            process_token_event(self._event(input=-100, output=50, total=-50), r_client=fake_redis)
            is False
        )
        assert fake_redis.hgetall("billing:usage:tenant_meter_1:2026-09") == {}
        assert len(fake_redis.lrange("telemetry:dlq", 0, -1)) == 1

    def test_a_total_below_its_parts_is_rejected(self, fake_redis):
        """The dashboard re-derives input plus output, so this never adds up."""
        assert (
            process_token_event(self._event(input=100, output=50, total=10), r_client=fake_redis)
            is False
        )

    def test_a_total_equal_to_its_parts_is_accepted(self, fake_redis):
        """Totals are compared, not trusted, so the honest case still lands."""
        assert (
            process_token_event(self._event(input=100, output=50, total=150), r_client=fake_redis)
            is True
        )
