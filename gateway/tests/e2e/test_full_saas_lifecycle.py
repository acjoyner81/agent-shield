"""Live end to end checks against the running Docker stack.

These drive the real services on localhost, not the in-process app, so they are
the only tests that catch the class of break the unit suite cannot see: an image
baked before a change landed, a service that never started, or a wiring mistake
between two containers.

**Everything here is asserted over HTTP.** An earlier version of this file read
Redis directly to check the Splunk queue. That could never work, for two
independent reasons, and both are worth keeping in mind before reaching for a
Redis client here again:

1. It connected to `localhost:6379`, which on this machine is the Homebrew
   `redis-server`, not the `agentshield-redis` container the gateway actually
   writes to. Verified with a canary key: the container sees a write from the
   gateway that `127.0.0.1` cannot see. The published port and the gateway's
   `redis://redis:6379` are different stores from the host's listener.
2. `POST /v1/keys` and the gateway write hashed digests under `apikey:{sha256}`,
   not the plaintext `apikey:{key}` this file seeded. The seeded key was
   therefore never a credential the gateway would accept.

So the tenant setup mints a real key through the API and every assertion reads
a response the gateway actually produced. `PYTEST_ADDOPTS` and the unit suite
are unaffected: this directory only runs when the services answer.

The Java gateway is not exercised here. `java-services/gateway-java` is still a
shell (three files, no `@RestController`, no `/v1/chat/completions` route), so
a test pointed at port 8080 asserts against an endpoint that does not exist. The
chat path under test is the Python gateway's, which is the one that serves it.
"""

import hashlib
import hmac
import json
import os
import socket
import time

import pytest
import httpx

FASTAPI_PROXY_URL = os.getenv("FASTAPI_PROXY_URL", "http://localhost:8000")
GATEWAY_JAVA_URL = os.getenv("GATEWAY_JAVA_URL", "http://localhost:8080")


def is_service_up(url: str) -> bool:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=1):
            return True
    except (OSError, ValueError):
        return False


pytestmark = pytest.mark.skipif(
    not is_service_up(FASTAPI_PROXY_URL),
    reason="E2E live services (Gateway) are not running on localhost",
)


def _stripe_signed_headers(payload: bytes, secret: str) -> dict[str, str]:
    """Sign a webhook body the way Stripe does.

    The route verifies the signature and answers 400 `Invalid signature` without
    one, so an unsigned or placeholder header cannot provision anything. The
    secret has to be the one the running container holds, so it is read from the
    same place the container is configured from.
    """
    timestamp = str(int(time.time()))
    digest = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return {
        "stripe-signature": f"t={timestamp},v1={digest}",
        "Content-Type": "application/json",
    }


def _webhook_secret() -> str:
    """Resolve the webhook signing secret from the environment, then `.env`."""
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if secret:
        return secret

    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        ".env",
    )
    try:
        with open(env_path) as handle:
            for line in handle:
                if line.startswith("STRIPE_WEBHOOK_SECRET="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass

    pytest.skip("STRIPE_WEBHOOK_SECRET is not set in the environment or .env")


@pytest.fixture
def live_tenant():
    """A real machine credential for tenant_alpha, minted through the API.

    `POST /v1/keys` needs `keys:write`, which the DEV_MODE stand-in token carries
    (`gateway/auth.py:48`), so this works against a dev stack without an Auth0
    login. The key is revoked at teardown so repeat runs do not accumulate live
    credentials in the store.
    """
    with httpx.Client(base_url=FASTAPI_PROXY_URL, timeout=15.0) as client:
        created = client.post(
            "/v1/keys",
            headers={"Authorization": "Bearer dev-mock-token"},
            json={"name": "e2e lifecycle probe", "permissions": ["tools:execute"]},
        )
        assert created.status_code == 201, created.text

        body = created.json()
        key_id = body["key_id"]
        secret = body["secret_key"]

        try:
            yield {"key_id": key_id, "secret": secret, "tenant_id": "tenant_alpha"}
        finally:
            client.delete(
                f"/v1/keys/{key_id}",
                headers={"Authorization": "Bearer dev-mock-token"},
            )


def test_01_stripe_webhook_provisioning():
    """A correctly signed checkout event is accepted and provisions a tenant.

    Previously asserted with the literal header `whsec_mock_signature`, which the
    signature check rejects by design, so this test could only ever fail. The
    400 it used to see is the security control working, not a broken endpoint.
    """
    payload = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer_details": {"email": "e2e_provisioning@sme.com"},
                "metadata": {"tier": "pro"},
                "customer": "cus_e2e_provisioning",
            }
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()

    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            f"{FASTAPI_PROXY_URL}/api/v1/billing/webhook",
            content=body,
            headers=_stripe_signed_headers(body, _webhook_secret()),
        )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "success"


def test_01b_unsigned_webhook_is_rejected():
    """The flip side of the test above: a forged signature provisions nothing.

    Worth pinning explicitly. `test_01` proves the happy path reaches the
    handler, which is exactly the condition under which a missing signature
    check would go unnoticed.
    """
    payload = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer_details": {"email": "e2e_forged@sme.com"},
                "metadata": {"tier": "pro"},
                "customer": "cus_e2e_forged",
            }
        },
    }

    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            f"{FASTAPI_PROXY_URL}/api/v1/billing/webhook",
            content=json.dumps(payload, separators=(",", ":")).encode(),
            headers={
                "stripe-signature": "t=1,v1=deadbeef",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 400
    assert "Invalid signature" in response.json()["detail"]


def test_02_gateway_authenticated_llm_execution(live_tenant):
    """A machine key reaches the LLM route and the tenant is echoed back."""
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            f"{FASTAPI_PROXY_URL}/v1/chat/completions",
            headers={
                "X-Tenant-API-Key": live_tenant["secret"],
                "Content-Type": "application/json",
            },
            json={"prompt": "Check splunk logs for recent HTTP 429 errors.", "model": "gpt-4o"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenant_id"] == live_tenant["tenant_id"]
    assert body["cost_usd"] >= 0


def test_03_semantic_cache_cost_reduction(live_tenant):
    """A repeated prompt is served from cache at no cost.

    Uses a prompt unique to this run. The cache key is a hash of tenant, model,
    temperature, and prompt (`gateway/main.py:606`), so a fixed literal would
    be answered from a previous run's entry and the test would pass without ever
    exercising the miss-then-hit path.
    """
    prompt = f"e2e semantic cache probe {os.getpid()} {time.time_ns()}"

    with httpx.Client(timeout=20.0) as client:
        first = client.post(
            f"{FASTAPI_PROXY_URL}/v1/chat/completions",
            headers={
                "X-Tenant-API-Key": live_tenant["secret"],
                "Content-Type": "application/json",
            },
            json={"prompt": prompt, "model": "gpt-4o"},
        )
        second = client.post(
            f"{FASTAPI_PROXY_URL}/v1/chat/completions",
            headers={
                "X-Tenant-API-Key": live_tenant["secret"],
                "Content-Type": "application/json",
            },
            json={"prompt": prompt, "model": "gpt-4o"},
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    assert first.json()["source"] == "llm_execution", "first call should miss the cache"
    assert second.json()["source"] == "semantic_cache", "second call should hit the cache"
    assert second.json()["cost_usd"] == 0.0
    assert second.json()["response"] == first.json()["response"]


def test_04_minted_key_is_usable_for_tool_execution(live_tenant):
    """The credential minted above works on a second, differently-gated route.

    Replaces an assertion on the raw Splunk queue length. That read the wrong
    Redis entirely (see the module docstring), so it was measuring a store the
    gateway never wrote to; a non-zero length there proved nothing about this
    system. This instead proves the key authenticates on `POST /v1/tools/execute`,
    which is gated by `tools:execute` and reached the MCP server.
    """
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            f"{FASTAPI_PROXY_URL}/v1/tools/execute",
            headers={
                "X-Tenant-API-Key": live_tenant["secret"],
                "Content-Type": "application/json",
            },
            json={"tool_name": "echo", "arguments": {"message": "e2e lifecycle probe"}},
        )

    assert response.status_code == 200, response.text


def test_05_revoked_key_stops_working(live_tenant):
    """A revoked credential is refused at the perimeter, not merely hidden.

    Complements `test_04`: minting a key that works is only half the lifecycle,
    and the tombstone path (`gateway/keys.py`) is what stops a leaked key from
    outliving its rotation.
    """
    with httpx.Client(base_url=FASTAPI_PROXY_URL, timeout=15.0) as client:
        before = client.get(
            "/v1/keys", headers={"X-Tenant-API-Key": live_tenant["secret"]}
        )
        assert before.status_code == 200, before.text

        revoked = client.delete(
            f"/v1/keys/{live_tenant['key_id']}",
            headers={"Authorization": "Bearer dev-mock-token"},
        )
        assert revoked.status_code in (200, 204), revoked.text

        after = client.get(
            "/v1/keys", headers={"X-Tenant-API-Key": live_tenant["secret"]}
        )

    assert after.status_code == 401
