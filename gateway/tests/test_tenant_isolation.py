"""No tenant may read another tenant's data (regression guard for the Spec 0012 leak).

`get_telemetry_logs` used to return five hardcoded rows when Redis held no
history for the requesting tenant. Those rows were not merely invented: they
carried `tenantId` values belonging to other tenants, so an empty tenant was
served another tenant's events. It was fixed to return `[]`, and this file is
what stops the same class of bug coming back through any tenant-scoped read.

These tests exist because every other suite here would have passed with the leak
in place. `test_portal_integration.py` asserts status codes and response shape
for a single tenant; nothing in the suite ever asked tenant A to read while
tenant B's data was present. A cross-tenant assertion is the only kind of test
that can fail on a bug that is invisible to a single-tenant test.

Every route resolves the tenant through `resolve_active_tenant`, which reads the
verified JWT claim or a hashed API key, so the tests drive two distinct tenants
via two distinct credentials rather than a header.
"""

import hashlib
import json
import os

import pytest
from fastapi.testclient import TestClient

from gateway import main as main_module
from gateway.main import app

client = TestClient(app)

TENANT_ALPHA = "tenant_alpha"
TENANT_BETA = "tenant_beta"

ALPHA_SECRET = "sk_test_isolation_alpha_000000"
BETA_SECRET = "sk_test_isolation_beta_0000000"


def seed_api_key(store, secret, tenant_id, key_id, permissions=("logs:read", "keys:write")):
    """Write a live key record the way `POST /v1/keys` would."""
    digest = hashlib.sha256(secret.encode()).hexdigest()
    store.set(f"apikey:{digest}", json.dumps({"tenant_id": tenant_id, "key_id": key_id}))
    meta = {
        "name": f"{tenant_id} key",
        "prefix": f"{secret[:10]}••••••••••••",
        "created_at": "2026-09-30T10:15:00+00:00",
        "status": "Active",
        "version": 1,
        "secret_hash": digest,
        "permissions": list(permissions),
    }
    store.hset(f"tenant:keys:{tenant_id}", key_id, json.dumps(meta))
    return meta


def seed_telemetry(store, tenant_id, event_id, message):
    """Append one event to the shared history list the telemetry route reads."""
    store.rpush(
        "telemetry_history",
        json.dumps(
            {
                "event_id": event_id,
                "tenant_id": tenant_id,
                "timestamp": "2026-09-30T10:15:00+00:00",
                "status_code": 200,
                "latency_ms": 120,
                "eval_passed": True,
                "message": message,
                "trace_id": f"trace-{event_id}",
            }
        ),
    )


@pytest.fixture(autouse=True)
def _dev_mode():
    """Enable the Auth0 stand-in path; this file is about isolation, not login."""
    previous = os.environ.get("DEV_MODE")
    os.environ["DEV_MODE"] = "true"
    yield
    if previous is None:
        os.environ.pop("DEV_MODE", None)
    else:
        os.environ["DEV_MODE"] = previous


@pytest.fixture
def two_tenants(fake_redis):
    """Seed both tenants' credentials, keys, telemetry, and spend."""
    seed_api_key(fake_redis, ALPHA_SECRET, TENANT_ALPHA, "key_alpha_1")
    seed_api_key(fake_redis, BETA_SECRET, TENANT_BETA, "key_beta_1")

    seed_telemetry(fake_redis, TENANT_ALPHA, "evt_alpha_1", "alpha completion")
    seed_telemetry(fake_redis, TENANT_BETA, "evt_beta_1", "beta completion")

    # Daily aggregate hashes are `usage:daily:{tenant}:{date}:{model}`
    # (metering.py:20), and the summary scans that pattern rather than reading a
    # single key, so the seed has to use the real key shape.
    fake_redis.hset(
        f"usage:daily:{TENANT_ALPHA}:2026-09-30:gpt-4o-mini",
        mapping={"input": 600, "output": 600, "total": 1200, "requests": 11},
    )
    fake_redis.hset(
        f"usage:daily:{TENANT_BETA}:2026-09-30:gpt-4o-mini",
        mapping={"input": 1200, "output": 1200, "total": 2400, "requests": 22},
    )
    fake_redis.hset(
        f"usage:daily:{TENANT_ALPHA}:2026-09-30:__meta__",
        mapping={"quality_passed": 9, "failed_requests": 1, "rate_limited_requests": 1},
    )
    fake_redis.hset(
        f"usage:daily:{TENANT_BETA}:2026-09-30:__meta__",
        mapping={"quality_passed": 18, "failed_requests": 2, "rate_limited_requests": 2},
    )
    return fake_redis


ALPHA = {"X-Tenant-API-Key": ALPHA_SECRET}
BETA = {"X-Tenant-API-Key": BETA_SECRET}


class _UnavailableStore:
    """A Redis stand-in whose every read raises, to drive the failure paths."""

    def __getattr__(self, name):
        def boom(*args, **kwargs):
            raise ConnectionError("store unavailable")

        return boom


class TestTelemetryIsolation:
    """The route that leaked. `/v1` and the portal's `/api/v1` share the handler."""

    @pytest.mark.parametrize("path", ["/v1/telemetry/logs", "/api/v1/telemetry/logs"])
    def test_each_tenant_sees_only_its_own_events(self, two_tenants, path):
        alpha = client.get(path, headers=ALPHA)
        beta = client.get(path, headers=BETA)

        assert alpha.status_code == 200
        assert beta.status_code == 200

        alpha_rows = alpha.json()
        beta_rows = beta.json()

        assert alpha_rows, "tenant A's own events should be returned to tenant A"
        assert beta_rows, "tenant B's own events should be returned to tenant B"

        assert {row["tenantId"] for row in alpha_rows} == {TENANT_ALPHA}
        assert {row["tenantId"] for row in beta_rows} == {TENANT_BETA}

        alpha_messages = {row["message"] for row in alpha_rows}
        beta_messages = {row["message"] for row in beta_rows}

        assert "beta completion" not in alpha_messages
        assert "alpha completion" not in beta_messages

    def test_a_tenant_with_no_history_gets_an_empty_list_not_another_tenants(self, fake_redis):
        """The exact regression. No history for the caller means `[]`, never a neighbour's rows."""
        seed_api_key(fake_redis, ALPHA_SECRET, TENANT_ALPHA, "key_alpha_1")
        seed_telemetry(fake_redis, TENANT_BETA, "evt_beta_1", "beta completion")

        rows = client.get("/v1/telemetry/logs", headers=ALPHA).json()

        assert rows == []

    def test_no_response_body_ever_mentions_another_tenant_id(self, two_tenants):
        """Belt and braces: assert on the serialized body, not the parsed rows.

        A leak through a field the typed view drops would survive the row
        assertions above, so this checks the bytes actually sent to the portal.
        """
        body = client.get("/v1/telemetry/logs", headers=ALPHA).text

        assert TENANT_BETA not in body
        assert "beta completion" not in body


class TestUsageSummaryIsolation:
    """`/summary` echoes `tenant_id`, so a wrong value is directly observable."""

    @pytest.mark.parametrize("path", ["/v1/usage/summary", "/api/v1/usage/summary"])
    def test_summary_reports_the_callers_own_tenant(self, two_tenants, path):
        alpha = client.get(path, headers=ALPHA)
        beta = client.get(path, headers=BETA)

        assert alpha.status_code == 200
        assert beta.status_code == 200

        assert alpha.json()["tenant_id"] == TENANT_ALPHA
        assert beta.json()["tenant_id"] == TENANT_BETA

    def test_totals_are_not_a_tenants_siblings(self, two_tenants):
        """Alpha and beta were seeded with distinct request counts; catch a shared roll-up."""
        alpha = client.get("/v1/usage/summary", headers=ALPHA).json()
        beta = client.get("/v1/usage/summary", headers=BETA).json()

        assert alpha["totals"] != beta["totals"], "both tenants returned the same usage totals"


class TestKeyIsolation:
    """Keys are stored per tenant; a shared read would hand out another tenant's credentials."""

    @pytest.mark.parametrize("path", ["/v1/keys", "/api/v1/keys"])
    def test_a_tenant_only_lists_its_own_keys(self, two_tenants, path):
        alpha = client.get(path, headers=ALPHA)
        beta = client.get(path, headers=BETA)

        assert alpha.status_code == 200
        assert beta.status_code == 200

        alpha_ids = {row["key_id"] for row in alpha.json()}
        beta_ids = {row["key_id"] for row in beta.json()}

        assert "key_alpha_1" in alpha_ids
        assert "key_beta_1" not in alpha_ids
        assert "key_beta_1" in beta_ids
        assert "key_alpha_1" not in beta_ids


class TestTenantIsNotCallerSupplied:
    """The tenant comes from the verified credential, never from a request header.

    A pre-auth caller must not be able to nominate a tenant by header, which is
    the other half of the same boundary: even with no valid credential at all,
    a guessed `X-Tenant-ID` must not widen the read.
    """

    def test_x_tenant_id_header_cannot_select_another_tenant(self, two_tenants):
        response = client.get(
            "/v1/telemetry/logs",
            headers={**ALPHA, "X-Tenant-ID": TENANT_BETA},
        )

        assert response.status_code == 200
        body = response.text

        assert TENANT_BETA not in body
        assert "beta completion" not in body
        assert {row["tenantId"] for row in response.json()} == {TENANT_ALPHA}

    def test_x_tenant_id_header_alone_authenticates_nothing(self, fake_redis):
        """With no credential, a supplied tenant header is a 401, not a read."""
        response = client.get(
            "/v1/telemetry/logs",
            headers={"X-Tenant-ID": TENANT_BETA},
        )

        assert response.status_code == 401


class TestFallbacksAreEmptyNotInvented:
    """A read that cannot reach its store must fail closed.

    The original leak lived in exactly this fallback path, so each tenant-scoped
    read gets a failing-store case. An invented or borrowed row is a leak; an
    error is just an error.
    """

    @pytest.mark.parametrize(
        "path",
        ["/v1/telemetry/logs", "/v1/usage/summary", "/v1/keys"],
    )
    def test_store_failure_does_not_return_another_tenants_data(self, two_tenants, path):
        """Force the store read itself to fail, so the fallback is actually reached.

        This has to swap the module-level Redis client out. The routes read a
        client constructed at import time (`main.r` and the `r_client`
        dependencies), so patching `redis.Redis.lrange` leaves the real object in
        place, the read succeeds, and the fallback never runs. An earlier version
        of this test did exactly that and passed with the original leak in place.
        """
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(main_module, "r", _UnavailableStore())

            failing = TestClient(app, raise_server_exceptions=False)
            response = failing.get(path, headers=ALPHA)

        # Either the route fails closed or it answers with nothing borrowed.
        # What it must never do is serve tenant B's rows.
        if response.status_code == 200:
            assert TENANT_BETA not in response.text
        else:
            assert response.status_code in (500, 503)

    def test_the_fallback_path_is_reachable(self, two_tenants):
        """Proves the guard above is not vacuous.

        If the store can never be made to fail, the fail-closed assertion proves
        nothing, and the leak this file exists to catch could return unnoticed.
        """
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(main_module, "r", _UnavailableStore())
            response = TestClient(app, raise_server_exceptions=False).get(
                "/v1/telemetry/logs", headers=ALPHA
            )

        # The telemetry route catches the error and answers with an empty list.
        # That is the path the borrowed rows used to occupy.
        assert response.status_code == 200
        assert response.json() == []
