"""Admin dashboard API tests (Spec 0011).

Covers the acceptance criteria for the two dashboard reads:
- AC-1: read time cost from the price card, including unpriced models.
- AC-2: cost fields present only for a principal holding billing:admin.
- AC-3: tenant scoped quality, failure, and rate limit counters.
- AC-4: per service health with status and latency, no tenant data.
- AC-6: every value stays scoped to the authenticated tenant.
"""

import os
import fnmatch
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from gateway.auth import resolve_active_tenant
from gateway.main import app
from gateway.metering import (
    META_MODEL,
    get_tenant_usage_summary,
    meta_daily_key,
    process_meta_event,
)
from gateway.pricing import estimate_cost_usd, get_prices, price_per_1k

client = TestClient(app)

# DEV_MODE tokens, see gateway.auth.verify_token_credentials
DEV_HEADERS = {"Authorization": "Bearer dev-mock-token"}  # permissions: tools:execute, logs:read
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
        r.lpush.assert_called_once_with("telemetry:dlq", "not json at all")

    def test_event_without_tenant_routes_to_dlq(self):
        r = MagicMock()
        r.sadd.return_value = 1
        event = self.make_event("agentshield.security.authz_failure", "evt_notenant")
        del event["tenant_id"]

        assert process_meta_event(event, r_client=r) is False
        r.hincrby.assert_not_called()


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
    def healthy_probes(self):
        with patch("gateway.main._probe_redis", new=AsyncMock()), patch(
            "gateway.main._probe_mcp", new=AsyncMock()
        ), patch("gateway.main._probe_stripe", new=AsyncMock()):
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
