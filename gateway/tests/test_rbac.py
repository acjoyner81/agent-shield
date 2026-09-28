"""RBAC enforcement tests (spec 0005).

Verifies that token `permissions` claims are strictly enforced:
- Valid tokens carrying the exact required scope pass (AC-1, AC-2).
- Authenticated tokens missing the scope get 403 with the exact detail (AC-3).
- Authorization failures are recorded in telemetry (AC-4).
- Default-deny: empty or unrelated permission sets never match.

Implementation under test: `gateway.auth.require_permission` (dependency
factory) → `get_verified_tenant` (tenant + permission binding) → `verify_jwt`
(Auth0 / DEV_MODE token verification).
"""

import hashlib
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from fastapi import Request

from gateway.auth import (
    get_verified_tenant,
    require_permission,
    resolve_active_tenant,
    verify_jwt,
)
from gateway.keys import get_redis_client as keys_get_redis_client
from gateway.main import app

REQUIRED_SCOPE = "tools:execute"
UNAUTHORIZED_DETAIL = f"Permission denied: missing required scope '{REQUIRED_SCOPE}'"

client = TestClient(app)

# Standalone app used to exercise require_permission against arbitrary scopes
# (billing:admin, chat:write, ...) without touching gateway routes/redis.
scope_app = FastAPI()


@scope_app.post("/scope-check")
async def scope_check(
    _ok: bool = Depends(require_permission("billing:admin")),
) -> dict[str, bool]:
    return {"allowed": True}


scope_client = TestClient(scope_app)


# App used to observe the state the real get_verified_tenant binds.
bind_app = FastAPI()


@bind_app.post("/bind-check")
async def bind_check(
    request: Request,
    tenant_id: str = Depends(get_verified_tenant),
) -> dict:
    return {
        "tenant_id": tenant_id,
        "state_tenant": request.state.tenant_id,
        "state_user": request.state.user_id,
        "state_permissions": sorted(request.state.permissions),
    }


bind_client = TestClient(bind_app)


def make_claims(permissions, sub="user_rbac", tenant="tenant_rbac") -> dict:
    return {
        "sub": sub,
        "https://agentshield.com/tenant_id": tenant,
        "permissions": permissions,
    }


def make_resolver(claims):
    """Returns a resolve_active_tenant override that binds the given claims."""
    async def _resolve(request: Request) -> str:
        request.state.tenant_id = claims["https://agentshield.com/tenant_id"]
        request.state.user_id = claims["sub"]
        request.state.permissions = set(claims.get("permissions", []))
        return claims["https://agentshield.com/tenant_id"]
    return _resolve


@pytest.fixture(autouse=True)
def _dev_mode_enabled():
    """DEV_MODE must be true for every test regardless of prior modules'
    teardown that may have removed/unset the env var (full-suite ordering)."""
    previous = os.environ.get("DEV_MODE")
    os.environ["DEV_MODE"] = "true"
    yield
    if previous is None:
        os.environ.pop("DEV_MODE", None)
    else:
        os.environ["DEV_MODE"] = previous


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()
    scope_app.dependency_overrides.clear()
    bind_app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _stub_rate_limit():
    with patch(
        "gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)
    ):
        yield


@pytest.fixture(autouse=True)
def _stub_telemetry():
    with patch("gateway.main.emit_request_completed") as req, patch(
        "gateway.auth.emit_authz_failure"
    ) as authz:
        req.return_value = ""
        authz.return_value = ""
        yield {"authz": authz, "request_completed": req}


# ---------------------------------------------------------------------------
# Route-level enforcement (real DEV_MODE token path + mocked MCP transport)
# ---------------------------------------------------------------------------

def test_rbac_execute_tool_success(_stub_telemetry):
    """AC-1, AC-2: dev-mock-token carries tools:execute -> request succeeds.

    Exercises the real chain: verify_jwt -> get_verified_tenant (binds
    tenant_id + permissions) -> require_permission pass -> MCP forward.
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": "success_output"}
    mock_response.raise_for_status = lambda: None

    mock_client_instance = MagicMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=None)

    with patch("gateway.main.httpx.AsyncClient", return_value=mock_client_instance):
        response = client.post(
            "/v1/tools/execute",
            headers={"Authorization": "Bearer dev-mock-token"},
            json={"tool_name": "test_tool", "params": {}},
        )
    assert response.status_code == 200
    assert response.json()["result"] == "success_output"

    # Authz was permitted: no authz failure telemetry emitted.
    _stub_telemetry["authz"].assert_not_called()


def test_rbac_execute_tool_forbidden(_stub_telemetry):
    """AC-3: dev-unprivileged-token lacks tools:execute -> 403 + authz telemetry."""
    response = client.post(
        "/v1/tools/execute",
        headers={"Authorization": "Bearer dev-unprivileged-token"},
        json={"tool_name": "test_tool", "params": {}},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": UNAUTHORIZED_DETAIL}

    # AC-4: failure recorded with user_id, tenant_id, required_permission.
    _stub_telemetry["authz"].assert_called_once_with(
        tenant_id="tenant_alpha",
        user_id="user_dev_456",
        trace_id="unknown",
        span_id="unknown",
        missing_scope=REQUIRED_SCOPE,
    )


def test_rbac_missing_bearer_rejected():
    """AC-1 (spec 0004): no Authorization header -> HTTP 401, not 403/200/5xx."""
    response = client.post(
        "/v1/tools/execute",
        json={"tool_name": "test_tool", "params": {}},
    )
    assert response.status_code == 401
    assert "Not authenticated" in response.json()["detail"]


def test_rbac_invalid_token_rejected():
    """Token verification failure propagates as 401 before any permission check."""
    async def _deny(request: Request):
        raise HTTPException(status_code=401, detail="Invalid access token")

    app.dependency_overrides[resolve_active_tenant] = _deny
    response = client.post(
        "/v1/tools/execute",
        headers={"Authorization": "Bearer bogus-token"},
        json={"tool_name": "test_tool", "params": {}},
    )
    assert response.status_code == 401
    assert "Invalid access token" in response.json()["detail"]


def test_rbac_tenant_binding_overrides_header():
    """AC-4 spec 0004: verified tenant from JWT wins over untrusted header.

    The attacker claims X-Tenant-ID: tenant_evil; the token is bound to
    tenant_rbac, so telemetry/failure context uses tenant_rbac.
    """
    app.dependency_overrides[resolve_active_tenant] = make_resolver(
        make_claims(["logs:read"], tenant="tenant_rbac")
    )
    response = client.post(
        "/v1/tools/execute",
        headers={
            "Authorization": "Bearer dev-mock-token",
            "X-Tenant-ID": "tenant_evil",
        },
        json={"tool_name": "test_tool", "params": {}},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": UNAUTHORIZED_DETAIL}


# ---------------------------------------------------------------------------
# require_permission semantics: exact-match, default-deny (arbitrary scopes)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "permissions",
    [
        ["billing:admin"],
        ["logs:read", "billing:admin", "tools:execute"],
        ["billing:read", "entitlements:view", "billing:admin", "org:write"],
    ],
)
def test_scope_present_passes(permissions):
    """AC-1, AC-2: exact scope present (alone or in a larger set) is allowed."""
    scope_app.dependency_overrides[resolve_active_tenant] = make_resolver(make_claims(permissions))
    response = scope_client.post(
        "/scope-check",
        headers={"Authorization": "Bearer dev-mock-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"allowed": True}


@pytest.mark.parametrize(
    "permissions",
    [
        [],
        ["logs:read"],
        ["billing:read"],
        ["tools:execute"],
        ["billing:admin:*"],
        ["*", "superadmin"],
    ],
)
def test_scope_absent_denied(permissions):
    """AC-2, AC-3: exact string match. No wildcard, no implicit roles."""
    scope_app.dependency_overrides[resolve_active_tenant] = make_resolver(make_claims(permissions))
    response = scope_client.post(
        "/scope-check",
        headers={"Authorization": "Bearer dev-mock-token"},
    )
    assert response.status_code == 403
    assert response.json() == {
        "detail": "Permission denied: missing required scope 'billing:admin'"
    }


# ---------------------------------------------------------------------------
# require_permission depends on get_verified_tenant (auth runs before authz)
# ---------------------------------------------------------------------------

def test_authz_never_runs_without_verified_tenant(_stub_telemetry):
    """Order of ops (spec 0005): a 401 from resolve_active_tenant short-circuits
    before require_permission can evaluate. Auth fails -> no 403 decision."""
    async def _deny(request: Request):
        raise HTTPException(status_code=401, detail="Invalid access token")

    # resolve_active_tenant raises; permission check never consulted.
    app.dependency_overrides[resolve_active_tenant] = _deny
    response = client.post(
        "/v1/tools/execute",
        headers={"Authorization": "Bearer expired-token"},
        json={"tool_name": "test_tool", "params": {}},
    )
    assert response.status_code == 401
    _stub_telemetry["authz"].assert_not_called()


def test_get_verified_tenant_binds_context():
    """get_verified_tenant populates request.state from verified claims and
    the returned tenant_id matches, overriding any client-supplied header."""
    bind_app.dependency_overrides[verify_jwt] = lambda: make_claims(
        ["tools:execute", "logs:read"], sub="usr_42", tenant="ten_x"
    )
    response = bind_client.post(
        "/bind-check",
        headers={
            "Authorization": "Bearer dev-mock-token",
            "X-Tenant-ID": "tenant_evil",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "ten_x"           # from JWT, not the header
    assert body["state_tenant"] == "ten_x"
    assert body["state_user"] == "usr_42"
    assert body["state_permissions"] == ["logs:read", "tools:execute"]


# ---------------------------------------------------------------------------
# Machine principals (spec 0010): API keys hit the same RBAC path (AC-7, AC-8)
# ---------------------------------------------------------------------------

class _FakeKeyStore:
    def __init__(self):
        self.data = {}
        self.hashes = {}

    def set(self, key, value):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)

    def hset(self, hash_key, field, value):
        self.hashes.setdefault(hash_key, {})[field] = value

    def hget(self, hash_key, field):
        return self.hashes.get(hash_key, {}).get(field)


def _seed_api_key(secret, permissions, tenant="tenant_rbac"):
    store = _FakeKeyStore()
    key_id = "key_machine_1"
    meta = {
        "name": "machine principal",
        "prefix": "sk_live_••••••••••••abcd",
        "created_at": "2026-09-23T00:00:00+00:00",
        "status": "Active",
        "version": 1,
        "secret_hash": hashlib.sha256(secret.encode()).hexdigest(),
        "permissions": permissions,
    }
    store.set(
        f"apikey:{meta['secret_hash']}",
        json.dumps({"tenant_id": tenant, "key_id": key_id}),
    )
    store.hset("tenant:keys:tenant_rbac", key_id, json.dumps(meta))
    return store


def _mcp_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": "tool_output"}
    mock_response.raise_for_status = lambda: None

    mock_client_instance = MagicMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=None)
    return patch("gateway.main.httpx.AsyncClient", return_value=mock_client_instance)


def test_machine_key_with_scope_executes(_stub_telemetry):
    """AC-7: an API key carrying tools:execute passes the RBAC gate."""
    store = _seed_api_key("sk_machine_priv", ["tools:execute"])
    with patch("gateway.auth.get_redis_client", return_value=store), _mcp_success():
        app.dependency_overrides[keys_get_redis_client] = lambda: store
        response = client.post(
            "/v1/tools/execute",
            headers={"X-Tenant-API-Key": "sk_machine_priv"},
            json={"tool_name": "echo", "params": {}},
        )
    assert response.status_code == 200
    assert response.json()["result"] == "tool_output"
    _stub_telemetry["authz"].assert_not_called()


def test_machine_key_without_scope_denied_and_store_tenant_wins(_stub_telemetry):
    """AC-7, AC-8: key lacking the scope gets 403, tenant bound from the store."""
    store = _seed_api_key("sk_machine_unpriv", ["logs:read"])
    with patch("gateway.auth.get_redis_client", return_value=store):
        app.dependency_overrides[keys_get_redis_client] = lambda: store
        response = client.post(
            "/v1/tools/execute",
            headers={
                "X-Tenant-API-Key": "sk_machine_unpriv",
                "X-Tenant-ID": "tenant_evil",
            },
            json={"tool_name": "echo", "params": {}},
        )
    assert response.status_code == 403
    assert response.json() == {"detail": UNAUTHORIZED_DETAIL}

    _stub_telemetry["authz"].assert_called_once_with(
        tenant_id="tenant_rbac",
        user_id="key_machine_1",
        trace_id="unknown",
        span_id="unknown",
        missing_scope=REQUIRED_SCOPE,
    )