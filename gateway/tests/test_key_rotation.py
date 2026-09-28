"""API key rotation and lifecycle tests (Spec 0010).

Covers AC-1 through AC-8 of spec 0010 (docs/specs/0010-api-key-rotation/index.md):
- AC-1, AC-2: hashed storage, digest-only index, Active keys authenticate.
- AC-3: rotation marks the old key Rotated and mints a successor that works now.
- AC-4: a Rotated key inside the grace window still works and emits a key_rotation event.
- AC-5: a Rotated key past the grace window is rejected with 401.
- AC-6: revocation is immediate and persisted as a Revoked tombstone.
- AC-7: permission allowlists are enforced on RBAC routes for machine principals.
- AC-8: unknown keys are rejected; the bound tenant always comes from the store.
"""

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from config.settings import settings
from gateway.auth import verify_api_key
from gateway.keys import get_redis_client as keys_get_redis_client
from gateway.main import app

client = TestClient(app)

SCOPE = "tools:execute"
DENIED_DETAIL = f"Permission denied: missing required scope '{SCOPE}'"


class FakePipeline:
    def __init__(self, fake):
        self.fake = fake
        self.ops = []
        self.watched = False
        self.in_transaction = False

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def watch(self, key):
        self.watched = True
        return True

    def multi(self):
        self.in_transaction = True
        return True

    def hset(self, hash_key, field, value):
        self.ops.append(("hset", hash_key, field, value))
        return self

    def set(self, key, value):
        self.ops.append(("set", key, value))
        return self

    def delete(self, key):
        self.ops.append(("delete", key))
        return self

    def execute(self):
        while self.ops:
            op = self.ops.pop(0)
            if op[0] == "hset":
                self.fake.hset(op[1], op[2], op[3])
            elif op[0] == "set":
                self.fake.set(op[1], op[2])
            elif op[0] == "delete":
                self.fake.delete(op[1])


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.hashes = {}

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    def get(self, key):
        return self.data.get(key)

    def delete(self, key):
        self.data.pop(key, None)

    def hset(self, hash_key, field, value):
        self.hashes.setdefault(hash_key, {})[field] = value

    def hget(self, hash_key, field):
        return self.hashes.get(hash_key, {}).get(field)

    def hgetall(self, hash_key):
        return dict(self.hashes.get(hash_key, {}))

    def pipeline(self):
        return FakePipeline(self)

    def meta(self, tenant_id, key_id):
        raw = self.hget(f"tenant:keys:{tenant_id}", key_id)
        return json.loads(raw) if raw else None


@pytest.fixture(autouse=True)
def enable_dev_mode():
    previous = os.environ.get("DEV_MODE")
    os.environ["DEV_MODE"] = "true"
    yield
    if previous is None:
        os.environ.pop("DEV_MODE", None)
    else:
        os.environ["DEV_MODE"] = previous


@pytest.fixture(autouse=True)
def fake_redis():
    fake = FakeRedis()
    app.dependency_overrides[keys_get_redis_client] = lambda: fake
    with patch("gateway.auth.get_redis_client", return_value=fake):
        yield fake
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def stub_rate_limit():
    with patch(
        "gateway.rate_limit.check_token_bucket", return_value=(True, 59, 60, 1, 0)
    ):
        yield


@pytest.fixture(autouse=True)
def stub_telemetry():
    with patch("gateway.main.emit_request_completed") as req, patch(
        "gateway.keys.emit_key_rotation"
    ) as key_evt, patch("gateway.auth.emit_key_rotation") as grace_evt, patch(
        "gateway.auth.emit_authz_failure"
    ) as authz:
        req.return_value = ""
        key_evt.return_value = ""
        grace_evt.return_value = ""
        authz.return_value = ""
        yield {
            "request_completed": req,
            "key_lifecycle": key_evt,
            "grace": grace_evt,
            "authz": authz,
        }


def _mock_mcp():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": "tool_output"}
    mock_response.raise_for_status = lambda: None

    mock_client_instance = MagicMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=None)
    return patch("gateway.main.httpx.AsyncClient", return_value=mock_client_instance)


def _create_key(name="app key", permissions=None):
    if permissions is None:
        permissions = ["tools:execute"]
    body = {"name": name}
    if permissions is not None:
        body["permissions"] = permissions
    response = client.post(
        "/v1/keys",
        headers={"Authorization": "Bearer dev-mock-token"},
        json=body,
    )
    assert response.status_code == 201
    return response.json()


def _rotate(key_id):
    return client.post(
        f"/v1/keys/{key_id}/rotate",
        headers={"Authorization": "Bearer dev-mock-token"},
        json={},
    )


def _call_tool(secret, headers=None):
    request_headers = {"X-Tenant-API-Key": secret}
    if headers:
        request_headers.update(headers)
    with _mock_mcp():
        return client.post(
            "/v1/tools/execute",
            headers=request_headers,
            json={"tool_name": "echo", "params": {}},
        )


def test_create_stores_digest_only_and_authenticates(fake_redis):
    """AC-1, AC-2: Active key authenticates; raw secret never written to Redis."""
    created = _create_key()
    secret = created["secret_key"]
    digest = hashlib.sha256(secret.encode()).hexdigest()

    index_raw = fake_redis.get(f"apikey:{digest}")
    assert index_raw is not None
    assert json.loads(index_raw) == {"tenant_id": "tenant_alpha", "key_id": created["key_id"]}

    meta = fake_redis.meta("tenant_alpha", created["key_id"])
    assert meta["secret_hash"] == digest
    assert meta["status"] == "Active"
    assert meta["version"] == 1

    for value in fake_redis.data.values():
        assert secret not in str(value)
    for values in fake_redis.hashes.values():
        for value in values.values():
            assert secret not in str(value)

    assert _call_tool(secret).status_code == 200


def test_rotate_mints_successor_and_marks_old_rotated(fake_redis, stub_telemetry):
    """AC-3: rotation present: old key Rotated with rotated_at + superseded_by."""
    created = _create_key()
    old_secret = created["secret_key"]
    old_id = created["key_id"]

    response = _rotate(old_id)
    assert response.status_code == 200
    new_key = response.json()
    assert new_key["status"] == "Active"
    assert new_key["version"] == 2
    assert new_key["key_id"] != old_id
    assert new_key["previous_key_id"] == old_id
    assert new_key["previous_key_rotated_at"] is not None
    assert new_key["previous_key_superseded_by"] == new_key["key_id"]

    old_meta = fake_redis.meta("tenant_alpha", old_id)
    assert old_meta["status"] == "Rotated"
    assert old_meta["rotated_at"] is not None
    assert old_meta["superseded_by"] == new_key["key_id"]
    assert old_meta["version"] == 1

    rotated_call = [
        c for c in stub_telemetry["key_lifecycle"].call_args_list if c.kwargs.get("action") == "rotate"
    ]
    assert rotated_call
    assert rotated_call[0].kwargs["key_id"] == old_id
    assert rotated_call[0].kwargs["status"] == "Rotated"
    assert rotated_call[0].kwargs["version"] == 1

    assert _call_tool(new_key["secret_key"]).status_code == 200


def test_rotated_key_inside_grace_still_works_and_emits_event(fake_redis, stub_telemetry):
    """AC-4: old key works inside the grace window and emits a key_rotation event."""
    created = _create_key()
    old_secret = created["secret_key"]
    old_id = created["key_id"]

    _rotate(old_id)

    response = _call_tool(old_secret)
    assert response.status_code == 200

    grace_calls = [
        c for c in stub_telemetry["grace"].call_args_list if c.kwargs.get("action") == "grace"
    ]
    assert grace_calls
    assert grace_calls[0].kwargs["key_id"] == old_id
    assert grace_calls[0].kwargs["status"] == "Rotated"
    assert grace_calls[0].kwargs["version"] == 1

    # A second in-window request in the same hour must not emit another event.
    assert _call_tool(old_secret).status_code == 200
    grace_calls_after = [
        c for c in stub_telemetry["grace"].call_args_list if c.kwargs.get("action") == "grace"
    ]
    assert len(grace_calls_after) == 1


def test_rotated_key_past_grace_rejected(fake_redis):
    """AC-5: past rotated_at + grace, the old key returns 401."""
    created = _create_key()
    old_secret = created["secret_key"]
    old_id = created["key_id"]

    _rotate(old_id)

    old_meta = fake_redis.meta("tenant_alpha", old_id)
    old_meta["rotated_at"] = (
        datetime.now(timezone.utc)
        - timedelta(seconds=settings.api_key_grace_period_seconds + 60)
    ).isoformat()
    fake_redis.hset("tenant:keys:tenant_alpha", old_id, json.dumps(old_meta))

    response = _call_tool(old_secret)
    assert response.status_code == 401
    assert "grace period expired" in response.json()["detail"]


def test_revoke_is_immediate_and_tombstones(fake_redis, stub_telemetry):
    """AC-6: revoked key rejected 401; tombstone persists in the key hash."""
    created = _create_key()
    secret = created["secret_key"]
    key_id = created["key_id"]

    response = client.delete(
        f"/v1/keys/{key_id}",
        headers={"Authorization": "Bearer dev-mock-token"},
    )
    assert response.status_code == 204

    assert _call_tool(secret).status_code == 401

    meta = fake_redis.meta("tenant_alpha", key_id)
    assert meta is not None
    assert meta["status"] == "Revoked"

    listed = client.get("/v1/keys", headers={"Authorization": "Bearer dev-mock-token"})
    assert any(k["key_id"] == key_id and k["status"] == "Revoked" for k in listed.json())

    revoke_calls = [
        c for c in stub_telemetry["key_lifecycle"].call_args_list if c.kwargs.get("action") == "revoke"
    ]
    assert revoke_calls
    assert revoke_calls[0].kwargs["key_id"] == key_id
    assert revoke_calls[0].kwargs["status"] == "Revoked"


def test_machine_principal_rbac_enforcement(stub_telemetry):
    """AC-7: keys enforce permission allowlists exactly like JWT principals."""
    unprivileged = _create_key(name="no-scope", permissions=["logs:read"])
    response = _call_tool(unprivileged["secret_key"])
    assert response.status_code == 403
    assert response.json() == {"detail": DENIED_DETAIL}

    assert stub_telemetry["authz"].called
    authz_kwargs = stub_telemetry["authz"].call_args.kwargs
    assert authz_kwargs["tenant_id"] == "tenant_alpha"
    assert authz_kwargs["missing_scope"] == SCOPE

    privileged = _create_key(name="with-scope", permissions=["tools:execute"])
    assert _call_tool(privileged["secret_key"]).status_code == 200


def test_unknown_key_rejected_and_tenant_bound_from_store(stub_telemetry):
    """AC-8: unknown keys are 401; a spoofed X-Tenant-ID never wins."""
    bogus = _call_tool("sk_live_not_a_real_key")
    assert bogus.status_code == 401
    assert "Unknown API key" in bogus.json()["detail"]
    assert stub_telemetry["request_completed"].call_count == 0

    created = _create_key(name="store-bound", permissions=["tools:execute"])
    response = _call_tool(created["secret_key"], headers={"X-Tenant-ID": "tenant_evil"})
    assert response.status_code == 200

    success_calls = [
        c for c in stub_telemetry["request_completed"].call_args_list
        if c.kwargs.get("tenant_id") == "tenant_alpha"
    ]
    assert success_calls


def test_rotate_rejects_rotated_and_revoked_keys():
    """API pattern: 409 for a key that already rotated or was revoked."""
    created = _create_key()
    key_id = created["key_id"]

    assert _rotate(key_id).status_code == 200
    second = _rotate(key_id)
    assert second.status_code == 409
    assert "already rotated" in second.json()["detail"]

    revoked = _create_key(name="revoked-then-rotate")
    client.delete(
        f"/v1/keys/{revoked['key_id']}",
        headers={"Authorization": "Bearer dev-mock-token"},
    )
    rotate_revoked = _rotate(revoked["key_id"])
    assert rotate_revoked.status_code == 409
    assert "revoked" in rotate_revoked.json()["detail"]


def test_list_returns_lifecycle_fields(fake_redis):
    """LIST surfaces status, version, rotated_at per the spec's API surface."""
    created = _create_key(name="lifecycle-view")
    key_id = created["key_id"]
    _rotate(key_id)

    listed = client.get("/v1/keys", headers={"Authorization": "Bearer dev-mock-token"})
    keys_by_id = {k["key_id"]: k for k in listed.json()}
    assert keys_by_id[key_id]["status"] == "Rotated"
    assert keys_by_id[key_id]["rotated_at"] is not None
    assert keys_by_id[key_id]["version"] == 1


def test_verify_api_key_grace_and_expiry_direct(fake_redis):
    """Direct unit check of grace comparison for a Rotated key."""
    created = _create_key()
    secret = created["secret_key"]
    digest = hashlib.sha256(secret.encode()).hexdigest()
    index = json.loads(fake_redis.get(f"apikey:{digest}"))
    key_id = index["key_id"]

    async def _verify(rotated_at):
        meta = fake_redis.meta("tenant_alpha", key_id)
        meta["status"] = "Rotated"
        meta["rotated_at"] = rotated_at
        fake_redis.hset("tenant:keys:tenant_alpha", key_id, json.dumps(meta))

        from starlette.datastructures import State
        from starlette.requests import Request

        scope = {"type": "http", "headers": [], "method": "POST", "path": "/v1/tools/execute"}
        request = Request(scope)
        request._state = State()
        with patch("gateway.auth.emit_key_rotation") as evt:
            try:
                await verify_api_key(request, secret)
                return {"ok": True, "grace_emitted": evt.called}
            except Exception as exc:
                return {"ok": False, "detail": getattr(exc, "detail", "")}

    inside = asyncio.run(_verify(datetime.now(timezone.utc).isoformat()))
    assert inside["ok"] is True
    assert inside["grace_emitted"] is True

    expired = asyncio.run(
        _verify(
            (
                datetime.now(timezone.utc)
                - timedelta(seconds=settings.api_key_grace_period_seconds + 60)
            ).isoformat()
        )
    )
    assert expired["ok"] is False
    assert "grace period expired" in expired["detail"]