"""The portal's audit stream: gateway events have to reach telemetry_history.

The Observability page reads `telemetry_history` and nothing else. The aggregator
only ever shipped to Splunk, and the single writer of that key was the Tripwire
ingest endpoint, so the page rendered an empty table for every real tenant while
the gateway went on emitting events - and it did so honestly, returning an empty
list rather than an error, so nothing anywhere reported a fault.

These tests pin the projection and, just as importantly, pin what the projection
must *not* claim. A key rotation is not a request: reporting it as a successful
200 of 0ms and $0.0000 made the Status, Latency, and Cost columns assert things
that never happened.
"""

import json

import pytest
from fastapi.testclient import TestClient

from gateway.main import app
from gateway.telemetry import (
    HISTORY_LIMIT,
    emit_authz_failure,
    emit_billing_subscription_changed,
    emit_event,
    emit_key_rotation,
    emit_rate_limit_exceeded,
    emit_request_completed,
    emit_token_usage,
)

client = TestClient(app)

# DEV_MODE token, see gateway.auth.verify_token_credentials
DEV_HEADERS = {"Authorization": "Bearer dev-mock-token"}

TENANT_ALPHA = "tenant_alpha"
TENANT_BETA = "tenant_beta"


def history_rows(store):
    """The stored audit rows, newest first, decoded."""
    return [json.loads(raw) for raw in store.lrange("telemetry_history", 0, -1)]


class TestProjection:
    """What a gateway event looks like once it is a row on the page."""

    def test_a_completed_request_carries_its_own_status_and_latency(self, fake_redis, dev_mode):
        emit_request_completed(
            tenant_id=TENANT_ALPHA,
            method="POST",
            path="/v1/tools/execute",
            status_code=200,
            latency_ms=142,
        )
        row = history_rows(fake_redis)[0]
        assert row["status_code"] == 200
        assert row["latency_ms"] == 142
        assert row["message"] == "POST /v1/tools/execute"

    def test_a_failed_request_keeps_its_own_error_status(self, fake_redis, dev_mode):
        emit_request_completed(
            tenant_id=TENANT_ALPHA,
            method="GET",
            path="/v1/keys",
            status_code=502,
            latency_ms=9,
        )
        assert history_rows(fake_redis)[0]["status_code"] == 502

    def test_a_key_rotation_claims_no_status_and_no_latency(self, fake_redis):
        # The regression: both fields defaulted, so every key rotation rendered
        # as a green 200 of 0ms in a column headed Status and Latency.
        emit_key_rotation(
            tenant_id=TENANT_ALPHA, key_id="key_abc123", action="revoke", status="Revoked"
        )
        row = history_rows(fake_redis)[0]
        assert "status_code" not in row
        assert "latency_ms" not in row
        assert "key_abc123" in row["message"]

    def test_a_rate_limit_drop_is_reported_as_429(self, fake_redis, dev_mode):
        emit_rate_limit_exceeded(tenant_id=TENANT_ALPHA)
        assert history_rows(fake_redis)[0]["status_code"] == 429

    def test_a_denied_call_is_reported_as_403(self, fake_redis, dev_mode):
        emit_authz_failure(tenant_id=TENANT_ALPHA)
        assert history_rows(fake_redis)[0]["status_code"] == 403

    def test_a_billing_change_claims_no_status(self, fake_redis, dev_mode):
        emit_billing_subscription_changed(tenant_id=TENANT_ALPHA, action="upgraded", tier="pro")
        row = history_rows(fake_redis)[0]
        assert "status_code" not in row
        assert "upgraded" in row["message"]

    def test_token_usage_carries_counts_but_no_status(self, fake_redis):
        # A model call is billed, but it is not an HTTP response, so the Status
        # column has nothing true to say about it.
        emit_token_usage(
            tenant_id=TENANT_ALPHA, input_tokens=600, output_tokens=400, model="gpt-4o"
        )
        row = history_rows(fake_redis)[0]
        assert "status_code" not in row
        assert row["model"] == "gpt-4o"
        assert row["total_tokens"] == 1000

    def test_every_event_type_reaches_the_history(self, fake_redis):
        emitters = [
            lambda: emit_request_completed(
                tenant_id=TENANT_ALPHA, method="GET", path="/v1/keys",
                status_code=200, latency_ms=5,
            ),
            lambda: emit_token_usage(
                tenant_id=TENANT_ALPHA, input_tokens=1, output_tokens=1, model="gpt-4o"
            ),
            lambda: emit_authz_failure(tenant_id=TENANT_ALPHA),
            lambda: emit_rate_limit_exceeded(tenant_id=TENANT_ALPHA),
            lambda: emit_key_rotation(
                tenant_id=TENANT_ALPHA, key_id="k1", action="create", status="Active"
            ),
            lambda: emit_billing_subscription_changed(
                tenant_id=TENANT_ALPHA, action="downgraded"
            ),
        ]
        for emit in emitters:
            emit()
        assert len(history_rows(fake_redis)) == len(emitters)

    def test_the_row_records_the_events_own_id(self, fake_redis):
        # The page's Event ID has to trace back to the CloudEvent Splunk received,
        # or an operator cannot join the two.
        emit_request_completed(
            tenant_id=TENANT_ALPHA, method="GET", path="/v1/keys",
            status_code=200, latency_ms=1,
        )
        assert history_rows(fake_redis)[0]["event_id"].startswith("evt_")

    def test_the_history_is_capped(self, fake_redis):
        for _ in range(HISTORY_LIMIT + 15):
            emit_authz_failure(tenant_id=TENANT_ALPHA)
        assert len(history_rows(fake_redis)) == HISTORY_LIMIT

    def test_the_event_still_reaches_the_queue(self, fake_redis):
        # The history write sits beside the enqueue, not instead of it.
        emit_request_completed(
            tenant_id=TENANT_ALPHA, method="GET", path="/v1/keys",
            status_code=200, latency_ms=1,
        )
        assert fake_redis.xlen("telemetry:queue") == 1

    def test_a_broken_history_write_does_not_lose_the_event(self):
        class NoHistory:
            """Accepts the enqueue, refuses the history write."""

            def __init__(self):
                self.queued = []

            def xadd(self, stream, fields, **kwargs):
                # `maxlen` is passed by the capped enqueue; accepting it here
                # keeps this fake honest about the real redis-py signature.
                self.queued.append(fields)
                return "1-0"

            def lpush(self, *args, **kwargs):
                raise RuntimeError("history unavailable")

        store = NoHistory()
        emit_event(
            tenant_id=TENANT_ALPHA,
            event_type="agentshield.security.authz_failure",
            data={},
            redis_client=store,
        )
        # The event still reached the queue, so the aggregator still ships it.
        assert len(store.queued) == 1


class TestAuditStreamReads:
    """What the Observability page is served."""

    def test_a_tenants_own_events_are_returned(self, fake_redis, dev_mode):
        emit_request_completed(
            tenant_id=TENANT_ALPHA, method="GET", path="/v1/keys",
            status_code=200, latency_ms=12,
        )
        response = client.get("/api/v1/telemetry/logs", headers=DEV_HEADERS)
        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == 1
        assert rows[0]["tenantId"] == TENANT_ALPHA
        assert rows[0]["message"] == "GET /v1/keys"
        assert rows[0]["latencyMs"] == 12

    def test_another_tenants_events_are_not_returned(self, fake_redis, dev_mode):
        emit_request_completed(
            tenant_id=TENANT_BETA, method="GET", path="/v1/keys",
            status_code=200, latency_ms=12,
        )
        assert client.get("/api/v1/telemetry/logs", headers=DEV_HEADERS).json() == []

    def test_the_event_id_traces_back_to_the_cloudevent(self, fake_redis, dev_mode):
        emit_authz_failure(tenant_id=TENANT_ALPHA)
        stored = history_rows(fake_redis)[0]["event_id"]
        row = client.get("/api/v1/telemetry/logs", headers=DEV_HEADERS).json()[0]
        assert row["eventId"] == f"EVT-{stored.removeprefix('evt_')[:8]}"

    def _serve_one_key_rotation(self):
        emit_key_rotation(
            tenant_id=TENANT_ALPHA, key_id="key_abc123", action="revoke", status="Revoked"
        )
        return client.get("/api/v1/telemetry/logs", headers=DEV_HEADERS).json()[0]

    def test_a_key_rotation_is_not_reported_as_a_successful_request(self, fake_redis, dev_mode):
        # Defaulting this to 200 made a key revocation render as a green 200 in a
        # column headed Status. There was no request, so there is no status.
        assert self._serve_one_key_rotation()["statusCode"] is None

    def test_a_key_rotation_reports_no_latency(self, fake_redis, dev_mode):
        assert self._serve_one_key_rotation()["latencyMs"] is None

    def test_a_key_rotation_reports_no_cost(self, fake_redis, dev_mode):
        # $0.0000 on a row that never spent anything is a fabricated figure.
        assert self._serve_one_key_rotation()["costUsd"] is None

    def test_token_usage_reports_a_cost_from_the_rate_card(self, fake_redis, dev_mode):
        # 1000 tokens of gpt-4o at 0.0025 per 1k.
        emit_token_usage(
            tenant_id=TENANT_ALPHA, input_tokens=600, output_tokens=400, model="gpt-4o"
        )
        row = client.get("/api/v1/telemetry/logs", headers=DEV_HEADERS).json()[0]
        assert row["costUsd"] == pytest.approx(0.0025)

    def test_a_rate_limit_drop_counts_as_a_failed_request(self, fake_redis, dev_mode):
        # "Rate limit drops" was permanently 0 because nothing ever carried a 429.
        emit_rate_limit_exceeded(tenant_id=TENANT_ALPHA)
        row = client.get("/api/v1/telemetry/logs", headers=DEV_HEADERS).json()[0]
        assert row["statusCode"] == 429

    def test_a_legacy_row_without_an_event_id_still_gets_one(self, fake_redis, dev_mode):
        # The Tripwire ingest path posts a bare payload with no CloudEvent id.
        fake_redis.rpush(
            "telemetry_history",
            json.dumps(
                {"tenant_id": "system", "level": "CRITICAL", "message": "FIM violation"}
            ),
        )
        rows = client.get("/api/v1/telemetry/logs", headers=DEV_HEADERS).json()
        assert rows == []  # a system row is not this tenant's to read

    def test_a_malformed_row_is_skipped_rather_than_failing_the_read(self, fake_redis, dev_mode):
        emit_authz_failure(tenant_id=TENANT_ALPHA)
        fake_redis.rpush("telemetry_history", "not json at all")
        response = client.get("/api/v1/telemetry/logs", headers=DEV_HEADERS)
        assert response.status_code == 200
        assert len(response.json()) == 1
