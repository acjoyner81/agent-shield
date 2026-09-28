"""AC-4: machine access works end to end on the four contract routes (Spec 0012).

The published schema advertises `X-Tenant-API-Key` on every contract route, so
each one must actually accept a machine key. This drives the real
`resolve_active_tenant` path against a fake key store.
"""

import json
import os
from unittest.mock import MagicMock, patch

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


@pytest.fixture(autouse=True)
def _dev_mode():
    os.environ["DEV_MODE"] = "true"
    yield
    os.environ.pop("DEV_MODE", None)


def seed_key_store(permissions=("tools:execute",)):
    """A Redis holding one active machine key for tenant_alpha."""
    store = MagicMock()
    digest = __import__("hashlib").sha256(b"sk_test_machine_key_0000000000").hexdigest()
    store.get.side_effect = lambda key, *a, **k: (
        json.dumps({"tenant_id": "tenant_alpha", "key_id": "key_machine_1"})
        if key == f"apikey:{digest}"
        else None
    )
    store.hget.side_effect = lambda key, *a, **k: (
        json.dumps({
            "name": "machine",
            "prefix": "sk_test_...masked",
            "created_at": "2026-09-24T10:15:00+00:00",
            "status": "Active",
            "version": 1,
            "permissions": list(permissions),
        })
        if key == "tenant:keys:tenant_alpha"
        else None
    )
    store.scan.return_value = (0, [])
    store.hgetall.return_value = {}
    store.get.return_value = None
    store.hgetall.side_effect = lambda key, *a, **k: {}
    return store


@pytest.fixture(autouse=True)
def _stub_quota():
    """Skip the real Redis backed tier quota; this suite is about auth."""
    app.dependency_overrides[verify_tenant_quota] = lambda: None
    yield
    app.dependency_overrides.pop(verify_tenant_quota, None)


MACHINE_HEADERS = {"X-Tenant-API-Key": "sk_test_machine_key_0000000000"}


def _body(method, path):
    if path == "/v1/chat/completions":
        return {"prompt": "hello"}
    if path == "/v1/tools/execute":
        return {"tool_name": "lookup_order", "params": {}}
    return None


@pytest.mark.parametrize("method,path", CONTRACT_CALLS)
def test_contract_route_accepts_a_machine_key(method, path):
    """AC-4: an API key authenticates every contract route (no 401)."""
    store = seed_key_store()

    with patch("gateway.auth.get_redis_client", return_value=store), patch(
        "gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)
    ), patch("gateway.keys.get_redis_client", return_value=store), patch(
        "gateway.metering.get_redis_client", return_value=store
    ), patch("gateway.main.r", store):
        call = getattr(client, method)
        kwargs = {"headers": MACHINE_HEADERS}
        if _body(method, path) is not None:
            kwargs["json"] = _body(method, path)
        response = call(path, **kwargs)

    assert response.status_code != 401, f"{method.upper()} {path} rejected a machine key"
    assert response.status_code != 403, f"{method.upper()} {path} denied a scoped machine key"


def test_spoofed_tenant_header_is_ignored():
    """AC-3: a caller supplied X-Tenant-ID does not change the bound tenant."""
    store = seed_key_store()
    hostile = {
        **MACHINE_HEADERS,
        "X-Tenant-ID": "tenant_someone_else",
    }

    with patch("gateway.auth.get_redis_client", return_value=store), patch(
        "gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)
    ), patch("gateway.keys.get_redis_client", return_value=store):
        response = client.get("/v1/keys", headers=hostile)

    assert response.status_code == 200, response.text
    # The store tenant, never the header.
    assert store.hget.call_args[0][0] == "tenant:keys:tenant_alpha"


def test_bearer_wins_over_api_key():
    """AC-2: with both credentials present the Bearer tenant is bound."""
    store = seed_key_store()
    both = {
        **MACHINE_HEADERS,
        "Authorization": "Bearer dev-mock-token",  # tenant_alpha
    }

    with patch("gateway.auth.get_redis_client", return_value=store), patch(
        "gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)
    ), patch("gateway.keys.get_redis_client", return_value=store), patch(
        "gateway.metering.get_redis_client", return_value=store
    ):
        response = client.get("/v1/usage/summary", headers=both)

    assert response.status_code == 200, response.text
    assert response.json()["tenant_id"] == "tenant_alpha"
    # A JWT principal never consults the key store index.
    assert not any(
        str(call).startswith("apikey:") for call in store.get.call_args_list
    )


def test_machine_key_without_scope_is_denied_tools():
    """AC-4: tools:execute is enforced for machine principals too."""
    store = seed_key_store(permissions=())

    with patch("gateway.auth.get_redis_client", return_value=store), patch(
        "gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)
    ), patch("gateway.auth.emit_authz_failure"):
        response = client.post(
            "/v1/tools/execute",
            headers=MACHINE_HEADERS,
            json={"tool_name": "lookup_order", "params": {}},
        )

    assert response.status_code == 403


def test_revoked_machine_key_is_rejected():
    """AC-4: the key lifecycle still governs machine callers."""
    store = seed_key_store()
    store.hget.side_effect = lambda key, *a, **k: (
        json.dumps({
            "name": "machine",
            "prefix": "sk_test_...masked",
            "created_at": "2026-09-24T10:15:00+00:00",
            "status": "Revoked",
            "version": 1,
            "permissions": ["tools:execute"],
        })
        if key == "tenant:keys:tenant_alpha"
        else None
    )

    with patch("gateway.auth.get_redis_client", return_value=store), patch(
        "gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)
    ):
        response = client.get("/v1/keys", headers=MACHINE_HEADERS)

    assert response.status_code == 401
    assert "revoked" in response.json()["detail"].lower()
