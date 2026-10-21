"""AC-2, AC-3, AC-4: machine access on the contract routes (Spec 0012).

The published schema advertises `X-Tenant-API-Key` on every contract route, so
each one must actually accept a machine key, and the rules the schema states out
loud - Bearer wins over key, a caller supplied `X-Tenant-ID` is inert, scopes are
enforced - must hold against the real `resolve_active_tenant` path. Every case
here runs against a seeded in-process key store, not a mock of one.
"""

import hashlib
import json
import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from gateway.main import app
from gateway.dependencies import verify_tenant_quota

client = TestClient(app)

CONTRACT_CALLS = [
    ("post", "/v1/chat/completions"),
    ("post", "/v1/tools/execute"),
    ("get", "/v1/usage/summary"),
    ("get", "/v1/keys"),
]

MACHINE_SECRET = "sk_test_machine_key_0000000000"
MACHINE_HEADERS = {"X-Tenant-API-Key": MACHINE_SECRET}


@pytest.fixture(autouse=True)
def _dev_mode():
    os.environ["DEV_MODE"] = "true"
    yield
    os.environ.pop("DEV_MODE", None)


@pytest.fixture(autouse=True)
def _stub_quota():
    """Skip the tier quota; this suite is about authentication and authorization."""
    app.dependency_overrides[verify_tenant_quota] = lambda: None
    yield
    app.dependency_overrides.pop(verify_tenant_quota, None)


@pytest.fixture(autouse=True)
def _stub_mcp():
    """A deterministic MCP tool response, so tool execution never leaves the process."""
    with patch(
        "gateway.main.call_mcp_tool",
        AsyncMock(return_value={"content": [{"type": "text", "text": "tool_output"}]}),
    ) as mcp:
        yield mcp


def seed_key(
    store,
    secret=MACHINE_SECRET,
    tenant_id="tenant_alpha",
    key_id="key_machine_1",
    permissions=("tools:execute",),
    status="Active",
):
    """Write a live key record the way `POST /v1/keys` would."""
    digest = hashlib.sha256(secret.encode()).hexdigest()
    store.set(f"apikey:{digest}", json.dumps({"tenant_id": tenant_id, "key_id": key_id}))
    meta = {
        "name": "machine",
        "prefix": "sk_test_••••••••••••0000",
        "created_at": "2026-09-24T10:15:00+00:00",
        "status": status,
        "version": 1,
        "secret_hash": digest,
        "permissions": list(permissions),
    }
    store.hset(f"tenant:keys:{tenant_id}", key_id, json.dumps(meta))
    return meta


def _body(method, path):
    if path == "/v1/chat/completions":
        return {"prompt": "hello"}
    if path == "/v1/tools/execute":
        return {"tool_name": "lookup_order", "params": {}}
    return None


def _call(method, path, headers):
    call = getattr(client, method)
    kwargs = {"headers": headers}
    if _body(method, path) is not None:
        kwargs["json"] = _body(method, path)
    return call(path, **kwargs)


@pytest.mark.parametrize("method,path", CONTRACT_CALLS)
def test_contract_route_accepts_a_machine_key(method, path, fake_redis):
    """AC-4: an API key authenticates every contract route (no 401)."""
    seed_key(fake_redis)

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = _call(method, path, MACHINE_HEADERS)

    assert response.status_code != 401, f"{method.upper()} {path} rejected a machine key"
    assert response.status_code != 403, f"{method.upper()} {path} denied a scoped machine key"


def test_spoofed_tenant_header_is_ignored(fake_redis):
    """AC-3: a caller supplied X-Tenant-ID does not change the bound tenant."""
    seed_key(fake_redis, tenant_id="tenant_beta")
    hostile = {**MACHINE_HEADERS, "X-Tenant-ID": "tenant_someone_else"}

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.get("/v1/usage/summary", headers=hostile)

    assert response.status_code == 200, response.text
    # The store tenant, never the header value.
    assert response.json()["tenant_id"] == "tenant_beta"


def test_bearer_wins_over_api_key(fake_redis):
    """AC-2: with both credentials present the Bearer tenant is bound.

    The key is seeded to a different tenant than the token carries, so the
    response alone discriminates which credential was used.
    """
    seed_key(fake_redis, tenant_id="tenant_beta")
    both = {**MACHINE_HEADERS, "Authorization": "Bearer dev-mock-token"}  # tenant_alpha

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.get("/v1/usage/summary", headers=both)

    assert response.status_code == 200, response.text
    assert response.json()["tenant_id"] == "tenant_alpha"


def test_garbage_bearer_does_not_fall_back_to_the_key(fake_redis):
    """AC-2: a present but invalid Bearer fails closed rather than downgrading."""
    seed_key(fake_redis)
    both = {**MACHINE_HEADERS, "Authorization": "Bearer not-a-real-token"}

    response = client.get("/v1/usage/summary", headers=both)

    assert response.status_code == 401
    assert "Invalid access token" in response.json()["detail"]


def test_non_bearer_authorization_is_rejected_outright(fake_redis):
    """AC-2: `Basic` credentials are a 401, not a silent fall through to the key."""
    seed_key(fake_redis)

    response = client.get(
        "/v1/usage/summary",
        headers={**MACHINE_HEADERS, "Authorization": "Basic ZGV2LW1vY2stdG9rZW46eA=="},
    )

    assert response.status_code == 401
    assert "Authorization" in response.json()["detail"]


def test_machine_key_without_scope_is_denied_tools(fake_redis):
    """AC-4: tools:execute is enforced for machine principals too."""
    seed_key(fake_redis, permissions=())

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)), patch(
        "gateway.auth.emit_authz_failure"
    ):
        response = client.post(
            "/v1/tools/execute",
            headers=MACHINE_HEADERS,
            json={"tool_name": "lookup_order", "params": {}},
        )

    assert response.status_code == 403


def test_revoked_machine_key_is_rejected(fake_redis):
    """AC-4: the key lifecycle still governs machine callers."""
    seed_key(fake_redis, status="Revoked")

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.get("/v1/keys", headers=MACHINE_HEADERS)

    assert response.status_code == 401
    assert "revoked" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Key management: a machine credential must not be able to widen its own reach
# ---------------------------------------------------------------------------

def test_scopeless_key_cannot_mint_keys(fake_redis):
    """Blocker: a key with no scopes cannot create another key."""
    seed_key(fake_redis, permissions=())

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.post(
            "/v1/keys",
            headers=MACHINE_HEADERS,
            json={"name": "escalated", "permissions": ["tools:execute"]},
        )

    assert response.status_code == 403
    assert "keys:write" in response.json()["detail"]


def test_key_cannot_grant_a_scope_it_does_not_hold(fake_redis):
    """Blocker: holding keys:write does not permit granting billing:admin."""
    seed_key(fake_redis, permissions=("keys:write",))

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.post(
            "/v1/keys",
            headers=MACHINE_HEADERS,
            json={"name": "billing-reader", "permissions": ["billing:admin"]},
        )

    assert response.status_code == 403
    assert "billing:admin" in response.json()["detail"]


def test_key_cannot_revoke_a_sibling_key(fake_redis):
    """Blocker: revoking is a keys:write operation like the rest."""
    seed_key(fake_redis, permissions=())
    seed_key(
        fake_redis,
        secret="sk_test_sibling_key_00000000000",
        key_id="key_sibling",
        permissions=("tools:execute",),
    )

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.delete("/v1/keys/key_sibling", headers=MACHINE_HEADERS)

    assert response.status_code == 403
    still_active = json.loads(fake_redis.hget("tenant:keys:tenant_alpha", "key_sibling"))
    assert still_active["status"] == "Active"


def test_machine_key_with_billing_admin_sees_cost(fake_redis):
    """The billing:admin gate is a permission check, not a JWT-only branch."""
    seed_key(fake_redis, permissions=("billing:admin",))
    fake_redis.hset(
        "usage:daily:tenant_alpha:2026-09-24:gpt-4o",
        mapping={"input_tokens": "10", "output_tokens": "5", "total_tokens": "15", "request_count": "1"},
    )

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.get(
            "/v1/usage/summary",
            headers=MACHINE_HEADERS,
            # The seed is dated 2026-09-24 and the endpoint defaults to the
            # current month, so the window has to be a query parameter. Sent as
            # headers it was ignored and the read returned an empty range.
            params={"start_date": "2026-09-01", "end_date": "2026-12-31"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["totals"]["estimated_cost_usd"] is not None
    assert response.json()["totals"]["total_tokens"] == 15


def test_machine_key_without_billing_admin_sees_no_cost(fake_redis):
    seed_key(fake_redis, permissions=("tools:execute",))
    fake_redis.hset(
        "usage:daily:tenant_alpha:2026-09-24:gpt-4o",
        mapping={"input_tokens": "10", "output_tokens": "5", "total_tokens": "15", "request_count": "1"},
    )

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.get(
            "/v1/usage/summary",
            headers=MACHINE_HEADERS,
            params={"start_date": "2026-09-01", "end_date": "2026-12-31"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["totals"]["estimated_cost_usd"] is None
    assert response.json()["totals"]["total_tokens"] == 15


# ---------------------------------------------------------------------------
# AC-3: the limiter never spends a bucket it has not authenticated
# ---------------------------------------------------------------------------

def test_spoofed_tenant_header_debits_nobody(fake_redis):
    """AC-3: an anonymous request with X-Tenant-ID creates no rate limit state."""
    response = client.get("/v1/usage/summary", headers={"X-Tenant-ID": "tenant_someone_else"})

    assert response.status_code == 401
    assert fake_redis.keys("rate_limit:*") == []


def test_limiter_cannot_answer_429_before_authentication(fake_redis):
    """AC-3: an exhausted victim bucket does not turn an anonymous call into a 429."""
    victim = "tenant_someone_else"
    fake_redis.hset(f"rate_limit:{victim}", mapping={"tokens": "0", "last_updated": "0"})
    fake_redis.expire(f"rate_limit:{victim}", 3600)

    anonymous = client.get(
        "/v1/usage/summary", headers={"X-Tenant-ID": victim, "X-Tenant-API-Key": MACHINE_SECRET}
    )
    assert anonymous.status_code == 401

    # The same bucket is still full for the tenant that actually owns it, which a
    # pre-auth debit would have drained.
    remaining = float(fake_redis.hget(f"rate_limit:{victim}", "tokens"))
    assert remaining == 0.0


# ---------------------------------------------------------------------------
# Scoping an existing key: the write behind the portal's "Manage" control
# ---------------------------------------------------------------------------

def test_key_scope_update_replaces_the_allowlist(fake_redis):
    """The allowlist is set, not merged, so the caller states the whole set."""
    seed_key(fake_redis, permissions=("keys:write", "tools:execute"))
    seed_key(
        fake_redis,
        secret="sk_test_target_key_000000000000",
        key_id="key_target",
        permissions=("tools:execute",),
    )

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.patch(
            "/v1/keys/key_target",
            headers=MACHINE_HEADERS,
            json={"permissions": ["keys:write", "tools:execute"]},
        )

    assert response.status_code == 200, response.text
    assert response.json()["permissions"] == ["keys:write", "tools:execute"]
    stored = json.loads(fake_redis.hget("tenant:keys:tenant_alpha", "key_target"))
    assert stored["permissions"] == ["keys:write", "tools:execute"]


def test_scope_update_can_empty_an_allowlist(fake_redis):
    """An empty set is a real request: a key that authenticates but cannot act."""
    seed_key(fake_redis, permissions=("keys:write",))
    seed_key(
        fake_redis,
        secret="sk_test_target_key_000000000000",
        key_id="key_target",
        permissions=("tools:execute",),
    )

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.patch(
            "/v1/keys/key_target", headers=MACHINE_HEADERS, json={"permissions": []}
        )

    assert response.status_code == 200, response.text
    assert response.json()["permissions"] == []


def test_scope_update_cannot_grant_a_scope_the_caller_lacks(fake_redis):
    """The escalation rule that governs creation governs rescoping too.

    Without it, a key holding only `keys:write` could widen a sibling it manages
    to `billing:admin`, minting reach it never held through a key write.
    """
    seed_key(fake_redis, permissions=("keys:write",))
    seed_key(
        fake_redis,
        secret="sk_test_target_key_000000000000",
        key_id="key_target",
        permissions=("tools:execute",),
    )

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.patch(
            "/v1/keys/key_target",
            headers=MACHINE_HEADERS,
            json={"permissions": ["billing:admin"]},
        )

    assert response.status_code == 403
    assert "billing:admin" in response.json()["detail"]
    stored = json.loads(fake_redis.hget("tenant:keys:tenant_alpha", "key_target"))
    assert stored["permissions"] == ["tools:execute"]


def test_scope_update_without_keys_write_is_forbidden(fake_redis):
    """Rescoping is a key write, so it needs the scope that governs key writes."""
    seed_key(fake_redis, permissions=("tools:execute",))

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.patch(
            "/v1/keys/key_machine_1", headers=MACHINE_HEADERS, json={"permissions": []}
        )

    assert response.status_code == 403
    assert "keys:write" in response.json()["detail"]


def test_scope_update_on_a_revoked_key_is_a_conflict(fake_redis):
    """A revoked key is a tombstone. Editing its scopes would revive a credential."""
    seed_key(fake_redis, permissions=("keys:write",))
    seed_key(
        fake_redis,
        secret="sk_test_target_key_000000000000",
        key_id="key_target",
        permissions=("tools:execute",),
        status="Revoked",
    )

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.patch(
            "/v1/keys/key_target", headers=MACHINE_HEADERS, json={"permissions": ["keys:write"]}
        )

    assert response.status_code == 409
    assert "revoked" in response.json()["detail"].lower()
    stored = json.loads(fake_redis.hget("tenant:keys:tenant_alpha", "key_target"))
    assert stored["permissions"] == ["tools:execute"]


def test_scope_update_of_an_unknown_key_is_not_found(fake_redis):
    seed_key(fake_redis, permissions=("keys:write",))

    with patch("gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)):
        response = client.patch(
            "/v1/keys/key_missing", headers=MACHINE_HEADERS, json={"permissions": ["keys:write"]}
        )

    assert response.status_code == 404
