"""Admin dashboard API tests (Spec 0011).

Covers the acceptance criteria for the two dashboard reads:
- AC-1: read time cost from the price card, including unpriced models.
- AC-2: cost fields present only for a principal holding billing:admin.
- AC-3: tenant scoped quality, failure, and rate limit counters.
- AC-4: per service health with status and latency, no tenant data.
- AC-6: every value stays scoped to the authenticated tenant.
"""

import json
import os
import fnmatch
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from gateway.auth import resolve_active_tenant
from gateway.main import (
    INTEGRITY_STALE_SECONDS,
    _probe_file_integrity,
    _probe_splunk,
    _verdict_age_seconds,
    app,
)
from gateway.metering import (
    META_MODEL,
    get_tenant_usage_summary,
    meta_daily_key,
    process_meta_event,
)
from gateway.pricing import estimate_cost_usd, get_prices, price_per_1k

client = TestClient(app)

# DEV_MODE tokens, see gateway.auth.verify_token_credentials
DEV_HEADERS = {"Authorization": "Bearer dev-mock-token"}  # permissions: tools:execute, logs:read, keys:write, telemetry:admin
UNPRIVILEGED_HEADERS = {"Authorization": "Bearer dev-unprivileged-token"}  # permissions: logs:read

BILLING_ADMIN_CLAIMS = {
    "sub": "user_admin_1",
    "https://api.agentshield.local/tenant_id": "tenant_alpha",
    "permissions": ["tools:execute", "billing:admin"],
}

TENANT_B_HEADERS_CLAIMS = {
    "sub": "user_admin_2",
    "https://api.agentshield.local/tenant_id": "tenant_beta",
    "permissions": ["billing:admin"],
}


def tenant_principal(claims):
    """Returns a resolve_active_tenant override that binds the given claims.

    `/v1/usage/summary` authenticates through `resolve_active_tenant` (spec 0012
    AC-4, machines may use an API key), so overrides target that dependency
    rather than `verify_jwt`.
    """
    async def _resolve(request: Request) -> str:
        tenant_id = claims["https://api.agentshield.local/tenant_id"]
        request.state.tenant_id = tenant_id
        request.state.user_id = claims["sub"]
        request.state.permissions = set(claims.get("permissions", []))
        return tenant_id
    return _resolve


@pytest.fixture(autouse=True)
def enable_dev_mode():
    os.environ["DEV_MODE"] = "true"
    yield
    os.environ.pop("DEV_MODE", None)


@pytest.fixture
def scripted_redis():
    """A fake Redis whose hashes and scan results the test controls."""
    client_mock = MagicMock()

    def build(keys_and_data):
        all_keys = [k for k, _ in keys_and_data]
        data = dict(keys_and_data)

        # Real Redis honours the scan MATCH pattern, so the fake must too or
        # tenant isolation cannot be observed.
        def scan(cursor, match=None, count=None):
            matched = [k for k in all_keys if match is None or fnmatch.fnmatch(k, match)]
            return 0, matched

        client_mock.scan.side_effect = scan
        client_mock.hgetall.side_effect = lambda key: data.get(key, {})
        return client_mock

    client_mock.build = build
    return client_mock


def model_key(tenant: str, date: str, model: str) -> str:
    return f"usage:daily:{tenant}:{date}:{model}"


# The seeds below are dated 2026-09-20, and `/summary` defaults to the current
# month start through today (metering.py:288-291). Every call that goes through
# the endpoint therefore has to name its window, or it silently reads an empty
# range once the calendar rolls past September. The direct
# `get_tenant_usage_summary` calls pass the same range positionally.
SEPTEMBER = {"start_date": "2026-09-01", "end_date": "2026-09-30"}


class TestPricing:
    """AC-1: cost comes from the rate card at read time."""

    def test_price_card_loads_from_disk(self):
        prices = get_prices()
        assert prices, "the shipped rate card should be readable"
        assert price_per_1k("gpt-4o") > 0

    def test_cost_math_is_total_tokens_over_one_thousand_times_rate(self):
        # gpt-4o is 0.0025 per 1k tokens, so 4000 tokens costs 4 * 0.0025
        assert estimate_cost_usd("gpt-4o", 4000) == pytest.approx(0.01)

    def test_cost_scales_with_token_count(self):
        assert estimate_cost_usd("gpt-4o", 8000) == pytest.approx(
            2 * estimate_cost_usd("gpt-4o", 4000)
        )

    def test_unpriced_model_costs_zero_without_error(self):
        assert estimate_cost_usd("some-unlisted-model", 100000) == 0.0

    def test_zero_tokens_cost_zero(self):
        assert estimate_cost_usd("gpt-4o", 0) == 0.0

    def test_price_list_edit_applies_to_the_next_read(self, tmp_path):
        """Key invariant: cost is never stored, a card edit changes the next response."""
        price_file = tmp_path / "prices.json"
        price_file.write_text('{"gpt-4o": 0.01}', encoding="utf-8")
        os.utime(price_file, (0, 0))

        with patch("gateway.pricing.settings") as mock_settings:
            mock_settings.prices_file = str(price_file)
            assert estimate_cost_usd("gpt-4o", 1000) == pytest.approx(0.01)

            price_file.write_text('{"gpt-4o": 0.02}', encoding="utf-8")
            os.utime(price_file, (100, 100))

            assert estimate_cost_usd("gpt-4o", 1000) == pytest.approx(0.02)


class TestMetaCounters:
    """AC-3: security and request events fold into the per date counters."""

    def make_event(self, event_type, event_id, data=None, date="2026-09-24"):
        return {
            "specversion": "1.0",
            "type": event_type,
            "event_id": event_id,
            "tenant_id": "tenant_alpha",
            "timestamp": f"{date}T10:00:00Z",
            "data": data or {},
        }

    def test_authz_failure_increments_failed_requests(self, fake_redis):
        assert process_meta_event(
            self.make_event("agentshield.security.authz_failure", "evt_a1"), r_client=fake_redis
        ) is True
        assert fake_redis.hget(
            "usage:daily:tenant_alpha:2026-09-24:__meta__", "failed_requests"
        ) == "1"

    def test_rate_limit_increments_rate_limited_requests(self, fake_redis):
        assert process_meta_event(
            self.make_event("agentshield.security.rate_limit_exceeded", "evt_r1"), r_client=fake_redis
        ) is True
        assert fake_redis.hget(
            "usage:daily:tenant_alpha:2026-09-24:__meta__", "rate_limited_requests"
        ) == "1"

    def test_request_completed_counts_quality_only_with_a_passing_eval_flag(self, fake_redis):
        meta_key = "usage:daily:tenant_alpha:2026-09-24:__meta__"

        process_meta_event(
            self.make_event(
                "agentshield.telemetry.request.completed",
                "evt_q1",
                data={"method": "POST", "status_code": 200, "eval_passed": True},
            ),
            r_client=fake_redis,
        )
        assert fake_redis.hget(meta_key, "quality_passed") == "1"

        process_meta_event(
            self.make_event(
                "agentshield.telemetry.request.completed",
                "evt_q2",
                data={"method": "POST", "status_code": 200},
            ),
            r_client=fake_redis,
        )
        assert fake_redis.hget(meta_key, "quality_passed") == "1", (
            "a request with no passing eval flag must not count toward quality"
        )

    def test_duplicate_event_is_not_counted_twice(self, fake_redis):
        event = self.make_event("agentshield.security.authz_failure", "evt_dup")

        assert process_meta_event(event, r_client=fake_redis) is True
        assert process_meta_event(event, r_client=fake_redis) is True

        assert fake_redis.hget(
            "usage:daily:tenant_alpha:2026-09-24:__meta__", "failed_requests"
        ) == "1"

    def test_malformed_event_routes_to_dlq(self):
        r = MagicMock()
        assert process_meta_event("not json at all", r_client=r) is False
        r.rpush.assert_called_once_with("telemetry:dlq", "not json at all")

    def test_event_without_tenant_routes_to_dlq(self):
        r = MagicMock()
        r.sadd.return_value = 1
        event = self.make_event("agentshield.security.authz_failure", "evt_notenant")
        del event["tenant_id"]

        assert process_meta_event(event, r_client=r) is False
        # Not hincrby: the roll-up is applied by a Lua script through eval, so
        # asserting on hincrby proved nothing about any code path that exists.
        r.eval.assert_not_called()
        r.rpush.assert_called_once_with("telemetry:dlq", json.dumps(event))


class TestUsageSummaryCost:
    """AC-1, AC-2: cost on the summary, gated on billing:admin."""

    def seed(self, scripted_redis, tenant="tenant_alpha"):
        return scripted_redis.build(
            [
                (
                    model_key(tenant, "2026-09-20", "gpt-4o"),
                    {
                        "input_tokens": "1000",
                        "output_tokens": "3000",
                        "total_tokens": "4000",
                        "request_count": "4",
                    },
                ),
                (
                    model_key(tenant, "2026-09-20", "unlisted-model"),
                    {
                        "input_tokens": "500",
                        "output_tokens": "500",
                        "total_tokens": "1000",
                        "request_count": "1",
                    },
                ),
            ]
        )

    def test_cost_computed_when_requested(self, scripted_redis):
        r = self.seed(scripted_redis)
        summary = get_tenant_usage_summary(
            "tenant_alpha", "2026-09-01", "2026-09-30", r_client=r, include_cost=True
        )

        by_model = {m.model: m for m in summary.by_model}
        assert by_model["gpt-4o"].cost_usd == pytest.approx(0.01)
        assert by_model["unlisted-model"].cost_usd == 0.0
        assert summary.totals.estimated_cost_usd == pytest.approx(0.01)

    def test_cost_is_null_when_not_requested(self, scripted_redis):
        r = self.seed(scripted_redis)
        summary = get_tenant_usage_summary(
            "tenant_alpha", "2026-09-01", "2026-09-30", r_client=r, include_cost=False
        )

        assert summary.totals.estimated_cost_usd is None
        assert all(m.cost_usd is None for m in summary.by_model)
        # usage is still reported
        assert summary.totals.total_tokens == 5000

    def test_endpoint_returns_cost_for_billing_admin(self, scripted_redis):
        r = self.seed(scripted_redis)
        app.dependency_overrides[resolve_active_tenant] = tenant_principal(BILLING_ADMIN_CLAIMS)
        try:
            with patch("gateway.metering.get_redis_client", return_value=r):
                response = client.get("/v1/usage/summary", params=SEPTEMBER)
        finally:
            app.dependency_overrides.pop(resolve_active_tenant, None)

        assert response.status_code == 200
        body = response.json()
        assert body["totals"]["estimated_cost_usd"] == pytest.approx(0.01)
        priced = {m["model"]: m for m in body["by_model"]}
        assert priced["gpt-4o"]["cost_usd"] == pytest.approx(0.01)
        assert priced["unlisted-model"]["cost_usd"] == 0.0

    def test_endpoint_nulls_cost_without_billing_admin(self, scripted_redis):
        """AC-2, AC-6: usage visible, spend withheld, not a 403."""
        r = self.seed(scripted_redis)
        with patch("gateway.metering.get_redis_client", return_value=r):
            response = client.get("/v1/usage/summary", headers=DEV_HEADERS, params=SEPTEMBER)

        assert response.status_code == 200
        body = response.json()
        assert body["totals"]["estimated_cost_usd"] is None
        assert all(m["cost_usd"] is None for m in body["by_model"])
        assert body["totals"]["total_tokens"] == 5000
        assert body["totals"]["total_requests"] == 5

    def test_endpoint_nulls_cost_for_unprivileged_principal(self, scripted_redis):
        r = self.seed(scripted_redis)
        with patch("gateway.metering.get_redis_client", return_value=r):
            response = client.get(
                "/v1/usage/summary", headers=UNPRIVILEGED_HEADERS, params=SEPTEMBER
            )

        assert response.status_code == 200
        assert response.json()["totals"]["estimated_cost_usd"] is None


class TestUsageSummaryCounters:
    """AC-3, AC-6: the __meta__ counters surface in the summary and stay tenant scoped."""

    def seed_with_meta(self, scripted_redis, tenant="tenant_alpha", date="2026-09-20"):
        return scripted_redis.build(
            [
                (
                    model_key(tenant, date, "gpt-4o"),
                    {
                        "input_tokens": "1000",
                        "output_tokens": "1000",
                        "total_tokens": "2000",
                        "request_count": "8",
                    },
                ),
                (
                    meta_daily_key(tenant, date),
                    {
                        "quality_passed": "6",
                        "failed_requests": "2",
                        "rate_limited_requests": "3",
                    },
                ),
            ]
        )

    def test_counters_are_summed_from_the_meta_key(self, scripted_redis):
        r = self.seed_with_meta(scripted_redis)
        summary = get_tenant_usage_summary(
            "tenant_alpha", "2026-09-01", "2026-09-30", r_client=r
        )

        assert summary.totals.quality_passed == 6
        assert summary.totals.failed_requests == 2
        assert summary.totals.rate_limited_requests == 3

    def test_meta_key_is_not_reported_as_a_model(self, scripted_redis):
        """The __meta__ key shares the date filter but must never appear as a model row."""
        r = self.seed_with_meta(scripted_redis)
        summary = get_tenant_usage_summary(
            "tenant_alpha", "2026-09-01", "2026-09-30", r_client=r
        )

        assert [m.model for m in summary.by_model] == ["gpt-4o"]
        assert META_MODEL not in [m.model for m in summary.by_model]

    def test_meta_key_does_not_inflate_token_totals(self, scripted_redis):
        r = self.seed_with_meta(scripted_redis)
        summary = get_tenant_usage_summary(
            "tenant_alpha", "2026-09-01", "2026-09-30", r_client=r
        )

        assert summary.totals.total_tokens == 2000
        assert summary.totals.total_requests == 8

    def test_counters_default_to_zero_when_absent(self, scripted_redis):
        r = scripted_redis.build(
            [(model_key("tenant_alpha", "2026-09-20", "gpt-4o"), {"total_tokens": "10", "request_count": "1"})]
        )
        summary = get_tenant_usage_summary(
            "tenant_alpha", "2026-09-01", "2026-09-30", r_client=r
        )

        assert summary.totals.quality_passed == 0
        assert summary.totals.failed_requests == 0
        assert summary.totals.rate_limited_requests == 0

    def test_summary_only_returns_the_requesting_tenants_numbers(self, scripted_redis):
        """AC-6: tenant A's response never carries tenant B's roll-ups."""
        r = scripted_redis.build(
            [
                (model_key("tenant_alpha", "2026-09-20", "gpt-4o"), {"total_tokens": "100", "request_count": "1"}),
                (model_key("tenant_beta", "2026-09-20", "gpt-4o"), {"total_tokens": "9999", "request_count": "77"}),
                (meta_daily_key("tenant_beta", "2026-09-20"), {"failed_requests": "42"}),
            ]
        )

        alpha = get_tenant_usage_summary("tenant_alpha", "2026-09-01", "2026-09-30", r_client=r)
        assert alpha.totals.total_tokens == 100
        assert alpha.totals.total_requests == 1
        assert alpha.totals.failed_requests == 0

    def test_each_tenant_sees_only_its_own_summary(self, scripted_redis):
        """AC-6: two principals, two isolated responses over the same Redis."""
        r = scripted_redis.build(
            [
                (model_key("tenant_alpha", "2026-09-20", "gpt-4o"), {"total_tokens": "100", "request_count": "1"}),
                (model_key("tenant_beta", "2026-09-20", "gpt-4o"), {"total_tokens": "9999", "request_count": "77"}),
            ]
        )

        app.dependency_overrides[resolve_active_tenant] = tenant_principal(BILLING_ADMIN_CLAIMS)
        try:
            with patch("gateway.metering.get_redis_client", return_value=r):
                alpha = client.get("/v1/usage/summary", params=SEPTEMBER)
        finally:
            app.dependency_overrides.pop(resolve_active_tenant, None)

        app.dependency_overrides[resolve_active_tenant] = tenant_principal(TENANT_B_HEADERS_CLAIMS)
        try:
            with patch("gateway.metering.get_redis_client", return_value=r):
                beta = client.get("/v1/usage/summary", params=SEPTEMBER)
        finally:
            app.dependency_overrides.pop(resolve_active_tenant, None)

        assert alpha.json()["tenant_id"] == "tenant_alpha"
        assert alpha.json()["totals"]["total_tokens"] == 100
        assert beta.json()["tenant_id"] == "tenant_beta"
        assert beta.json()["totals"]["total_tokens"] == 9999

    def test_counters_ignore_dates_outside_the_period(self, scripted_redis):
        r = scripted_redis.build(
            [
                (model_key("tenant_alpha", "2026-08-01", "gpt-4o"), {"total_tokens": "500", "request_count": "5"}),
                (meta_daily_key("tenant_alpha", "2026-08-01"), {"failed_requests": "9"}),
            ]
        )
        summary = get_tenant_usage_summary(
            "tenant_alpha", "2026-09-01", "2026-09-30", r_client=r
        )

        assert summary.totals.total_tokens == 0
        assert summary.totals.failed_requests == 0


class TestHealthServices:
    """AC-4: one entry per service with a status and latency, and no tenant data."""

    @pytest.fixture(autouse=True)
    def healthy_probes(self, fake_redis):
        # Splunk is patched in too. A probe added to this surface is exercised by
        # every "everything is healthy" assertion here, so leaving the real one
        # running makes this file's tests depend on whether the developer happens
        # to have a collector configured.
        #
        # fake_redis is required, not incidental: the dev stand-in holds
        # telemetry:admin, so the response carries the metrics object and the
        # dead letter counts are read over Redis.
        with patch("gateway.main._probe_redis", new=AsyncMock()), patch(
            "gateway.main._probe_mcp", new=AsyncMock()
        ), patch("gateway.main._probe_stripe", new=AsyncMock()), patch(
            "gateway.main._probe_file_integrity", new=AsyncMock()
        ), patch("gateway.main._probe_splunk", new=AsyncMock()):
            yield

    def test_lists_gateway_mcp_and_redis_with_status_and_latency(self):
        response = client.get("/v1/health/services", headers=DEV_HEADERS)

        assert response.status_code == 200
        services = {s["name"]: s for s in response.json()["services"]}
        assert {"gateway", "mcp-server", "redis"} <= set(services)
        for service in services.values():
            assert service["status"] == "healthy"
            assert isinstance(service["latency_ms"], (int, float))
            assert service["latency_ms"] >= 0

    def test_reports_overall_status(self):
        assert client.get("/v1/health/services", headers=DEV_HEADERS).json()["overall"] == "healthy"

    def test_redis_outage_flips_status_to_degraded(self):
        with patch(
            "gateway.main._probe_redis", new=AsyncMock(side_effect=OSError("connection refused"))
        ):
            response = client.get("/v1/health/services", headers=DEV_HEADERS)

        body = response.json()
        services = {s["name"]: s for s in body["services"]}
        assert services["redis"]["status"] == "degraded"
        assert services["gateway"]["status"] == "healthy"
        assert body["overall"] == "degraded"

    def test_mcp_outage_flips_only_mcp(self):
        with patch("gateway.main._probe_mcp", new=AsyncMock(side_effect=OSError("refused"))):
            services = {
                s["name"]: s
                for s in client.get("/v1/health/services", headers=DEV_HEADERS).json()["services"]
            }

        assert services["mcp-server"]["status"] == "degraded"
        assert services["redis"]["status"] == "healthy"

    def test_stripe_appears_only_when_configured(self):
        with patch("gateway.main.settings") as mock_settings:
            mock_settings.stripe_api_key = ""
            without = client.get("/v1/health/services", headers=DEV_HEADERS).json()
        assert "stripe" not in {s["name"] for s in without["services"]}

        with patch("gateway.main.settings") as mock_settings:
            mock_settings.stripe_api_key = "sk_test_configured"
            with_stripe = client.get("/v1/health/services", headers=DEV_HEADERS).json()
        assert "stripe" in {s["name"] for s in with_stripe["services"]}

    def test_response_exposes_no_tenant_data(self):
        body = client.get("/v1/health/services", headers=DEV_HEADERS).text
        for leak in ["tenant_alpha", "tenant_id", "usage", "tokens"]:
            assert leak not in body

    def test_requires_authentication(self):
        assert client.get("/v1/health/services").status_code == 401

    def test_served_under_the_portal_api_alias(self):
        """The portal calls /api/v1/health/services through the Angular dev proxy."""
        assert client.get("/api/v1/health/services", headers=DEV_HEADERS).status_code == 200


class TestFileIntegrityProbe:
    """The integrity monitor reports on the service health surface.

    Tripwire's telemetry alert landed under tenant_id "system", which no real
    tenant reads, so integrity alerts were invisible to every human. The verdict
    file is how platform state reaches the health surface without adding another
    unauthenticated write endpoint that anyone could spoof.
    """

    @pytest.fixture
    def verdict_path(self, tmp_path):
        return tmp_path / "verdict.json"

    @pytest.fixture(autouse=True)
    def redis_for_metrics(self, fake_redis):
        """The dev stand-in holds telemetry:admin, so the health response reads
        the dead letter depth over Redis before it is returned."""
        return fake_redis

    def _write(self, path, **fields):
        payload = {"verdict": "clean", "objects": 112, "violations": 0, "host": "agentshield-fim"}
        payload.update(fields)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _fresh_timestamp(self, seconds_ago=0):
        moment = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    async def _probe(self, path):
        with patch("gateway.main.INTEGRITY_VERDICT_PATH", str(path)):
            return await _probe_file_integrity()

    @pytest.mark.asyncio
    async def test_a_fresh_clean_verdict_is_healthy(self, verdict_path):
        self._write(verdict_path, checked_at=self._fresh_timestamp())

        assert await self._probe(verdict_path) is None

    @pytest.mark.asyncio
    async def test_violations_are_degraded_and_name_the_count(self, verdict_path):
        self._write(
            verdict_path,
            verdict="violations",
            violations=7,
            objects=112,
            checked_at=self._fresh_timestamp(),
        )

        with pytest.raises(RuntimeError, match="7 of 112 monitored objects"):
            await self._probe(verdict_path)

    @pytest.mark.asyncio
    async def test_a_policy_mismatch_reports_the_reason_not_an_empty_count(self, verdict_path):
        """A policy mismatch scanned nothing, so both counts are legitimately zero.

        Reporting that as "0 of 0 monitored objects differ" would read like a
        healthy monitor that found nothing to complain about. The truth is the
        opposite: nothing was verified at all, so the monitor's own reason has to
        reach the operator instead of a count that says nothing.
        """
        self._write(
            verdict_path,
            verdict="error",
            violations=0,
            objects=0,
            reason="Policy no longer matches the baseline, so nothing was verified.",
            checked_at=self._fresh_timestamp(),
        )

        with pytest.raises(RuntimeError, match="nothing was verified"):
            await self._probe(verdict_path)

    @pytest.mark.asyncio
    async def test_a_blank_reason_falls_back_to_the_count(self, verdict_path):
        """An absent or empty reason must not turn the probe into a silent pass."""
        self._write(
            verdict_path,
            verdict="error",
            violations=2,
            objects=50,
            reason="   ",
            checked_at=self._fresh_timestamp(),
        )

        with pytest.raises(RuntimeError, match="2 of 50 monitored objects"):
            await self._probe(verdict_path)

    @pytest.mark.asyncio
    async def test_a_clean_verdict_that_still_reports_violations_is_degraded(self, verdict_path):
        """The count is trusted over the verdict string.

        The monitor writes both, so they should agree. If they ever disagree the
        nonzero count is the one that describes tampering, so it wins.
        """
        self._write(verdict_path, verdict="clean", violations=3, checked_at=self._fresh_timestamp())

        with pytest.raises(RuntimeError, match="3 of"):
            await self._probe(verdict_path)

    @pytest.mark.asyncio
    async def test_a_stale_verdict_is_degraded(self, verdict_path):
        """A monitor that stopped reporting must not read as a healthy one."""
        self._write(verdict_path, checked_at=self._fresh_timestamp(seconds_ago=INTEGRITY_STALE_SECONDS + 60))

        with pytest.raises(RuntimeError, match="old"):
            await self._probe(verdict_path)

    @pytest.mark.asyncio
    async def test_a_missing_verdict_is_degraded(self, verdict_path):
        with pytest.raises(RuntimeError, match="no Tripwire verdict"):
            await self._probe(verdict_path)

    @pytest.mark.asyncio
    async def test_an_unreadable_verdict_is_degraded(self, verdict_path):
        verdict_path.write_text("{not json", encoding="utf-8")

        with pytest.raises(RuntimeError, match="unreadable"):
            await self._probe(verdict_path)

    @pytest.mark.asyncio
    async def test_a_verdict_without_a_usable_timestamp_is_degraded(self, verdict_path):
        """A timestamp that cannot be parsed means the age is unknown.

        Treating an unknown age as fresh would let a stale monitor pass, which is
        the exact failure this probe exists to catch.
        """
        self._write(verdict_path, checked_at="not-a-timestamp")

        with pytest.raises(RuntimeError, match="no usable timestamp"):
            await self._probe(verdict_path)

    def test_verdict_age_is_measured_against_utc(self):
        assert _verdict_age_seconds(self._fresh_timestamp()) == pytest.approx(0, abs=5)

    def test_verdict_age_rejects_an_unparseable_timestamp(self):
        assert _verdict_age_seconds("") is None
        assert _verdict_age_seconds("2026-13-45T99:99:99Z") is None

    def test_it_appears_in_the_service_list(self):
        """The probe has to be wired into the route, not merely defined."""
        with patch("gateway.main._probe_redis", new=AsyncMock()), patch(
            "gateway.main._probe_mcp", new=AsyncMock()
        ), patch("gateway.main._probe_file_integrity", new=AsyncMock()):
            names = {
                s["name"]
                for s in client.get("/v1/health/services", headers=DEV_HEADERS).json()["services"]
            }

        assert "file-integrity" in names

    def test_a_degraded_monitor_flips_overall_and_explains_itself(self):
        """A red tile with no reason is not actionable, so the reason travels."""
        with patch("gateway.main._probe_redis", new=AsyncMock()), patch(
            "gateway.main._probe_mcp", new=AsyncMock()
        ), patch(
            "gateway.main._probe_file_integrity",
            new=AsyncMock(side_effect=RuntimeError("3 of 112 monitored objects differ")),
        ):
            body = client.get("/v1/health/services", headers=DEV_HEADERS).json()

        services = {s["name"]: s for s in body["services"]}
        assert services["file-integrity"]["status"] == "degraded"
        assert "3 of 112" in services["file-integrity"]["detail"]
        assert body["overall"] == "degraded"

    def test_health_still_exposes_no_tenant_data(self):
        """Adding an infrastructure signal must not widen what this route reveals."""
        with patch("gateway.main._probe_redis", new=AsyncMock()), patch(
            "gateway.main._probe_mcp", new=AsyncMock()
        ), patch(
            "gateway.main._probe_file_integrity",
            new=AsyncMock(side_effect=RuntimeError("1 of 112 monitored objects differ")),
        ):
            body = client.get("/v1/health/services", headers=DEV_HEADERS).text

        for leak in ["tenant_alpha", "tenant_id", "usage", "tokens"]:
            assert leak not in body


class TestSplunkProbe:
    """AC-7: the splunk entry tells an idle stack from a stalled one.

    An idle queue whose last ship succeeded long ago is healthy, however old
    that ship is. Work that waits with no recent attempt, an entry claimed but
    never acknowledged, and a failure streak with no success inside the window
    are each degraded, each with a reason that names itself.
    """

    QUEUE = "telemetry:queue"
    GROUP = "telemetry-aggregator"

    @pytest.fixture(autouse=True)
    def ready(self, fake_redis, monkeypatch):
        monkeypatch.setattr("gateway.main.SPLUNK_HEC_TOKEN_ENV", "test-token")
        # /dev/null exists, so the certificate authority check passes.
        monkeypatch.setattr("gateway.main.SPLUNK_HEC_CA", os.devnull)
        return fake_redis

    @staticmethod
    def _collector_answers(monkeypatch):
        """A reachable collector, so only the Redis side of the probe decides."""

        class Response:
            def raise_for_status(self):
                return None

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, *args, **kwargs):
                return Response()

        monkeypatch.setattr("gateway.main.httpx.AsyncClient", Client)

    @staticmethod
    def _receipt(r, **extra):
        fields = {
            "last_attempt": str(time.time() - 10_000),
            "last_success": str(time.time() - 10_000),
            "consecutive_failures": "0",
            "last_error": "",
        }
        fields.update(extra)
        r.hset("telemetry:ship", mapping=fields)

    @staticmethod
    def _waiting_event(r):
        # The entries have to exist before the group does: that is the order in
        # which the group's lag counts them as undelivered. Two of them, because
        # fakeredis reports a lag one short for a group that has never
        # delivered, while real Redis counts both.
        r.xadd("telemetry:queue", {"payload": '{"event_id": "evt_waiting"}'})
        r.xadd("telemetry:queue", {"payload": '{"event_id": "evt_waiting_too"}'})
        r.xgroup_create("telemetry:queue", "telemetry-aggregator", id="0", mkstream=True)

    @pytest.mark.asyncio
    async def test_an_idle_queue_with_old_receipts_is_healthy(self, ready, monkeypatch):
        """AC-7: an idle queue and no failures is healthy, however old the ship."""
        self._receipt(ready)
        self._collector_answers(monkeypatch)

        await _probe_splunk()

    @pytest.mark.asyncio
    async def test_waiting_events_with_a_stale_attempt_degrade_with_the_count(self, ready, monkeypatch):
        self._receipt(ready)
        self._waiting_event(ready)
        self._collector_answers(monkeypatch)

        with pytest.raises(RuntimeError, match="events are queued and the last ship attempt"):
            await _probe_splunk()

    @pytest.mark.asyncio
    async def test_a_claimed_but_unacknowledged_event_counts_as_waiting(self, ready, monkeypatch):
        """An entry in the pending list has been delivered to the consumer but
        confirmed by no one, so it is waiting work even with a stream lag of 0."""
        self._receipt(ready)
        ready.xadd("telemetry:queue", {"payload": '{"event_id": "evt_claimed"}'})
        ready.xgroup_create("telemetry:queue", "telemetry-aggregator", id="0", mkstream=True)
        ready.xreadgroup(
            "telemetry-aggregator",
            "some-consumer",
            streams={"telemetry:queue": ">"},
        )
        self._collector_answers(monkeypatch)

        with pytest.raises(RuntimeError, match="1 events are queued"):
            await _probe_splunk()

    @pytest.mark.asyncio
    async def test_events_that_never_shipped_degrade_on_a_cold_start(self, ready, monkeypatch):
        ready.xadd("telemetry:queue", {"payload": '{"event_id": "evt_cold"}'})
        self._collector_answers(monkeypatch)

        with pytest.raises(RuntimeError, match="no ship has ever succeeded"):
            await _probe_splunk()

    @pytest.mark.asyncio
    async def test_a_stale_failure_streak_degrades_with_its_reason(self, ready, monkeypatch):
        """A failure streak older than the window stays degraded even when the
        queue drained, because nothing has succeeded inside the window since."""
        self._receipt(
            ready,
            consecutive_failures="3",
            last_error="collector refused the batch: code=3",
        )
        self._collector_answers(monkeypatch)

        with pytest.raises(RuntimeError, match="3 consecutive failures.*collector refused"):
            await _probe_splunk()
