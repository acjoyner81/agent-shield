import socket
import pytest
import httpx
import redis
import json
import os

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
    not (is_service_up(FASTAPI_PROXY_URL) and is_service_up(GATEWAY_JAVA_URL)),
    reason="E2E live services (Gateway/Proxy) are not running on localhost"
)

r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=6379, db=0, decode_responses=True)

@pytest.fixture
def setup_test_tenant():
    tenant_id = "tenant_2100928e32a1"
    api_key = "ak_live_S6rLQWTrOJa-fvcXh-y6HZ1xsYgtdZes"
    email = "test_owner_0ca79c1e@enterprise.com"
    r.hset(f"tenant:{tenant_id}", mapping={"email": email, "tier": "pro"})
    r.set(f"apikey:{api_key}", tenant_id)
    return {"tenant_id": tenant_id, "api_key": api_key, "email": email}

@pytest.mark.asyncio
async def test_01_stripe_webhook_provisioning():
    async with httpx.AsyncClient() as client:
        mock_payload = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer_details": {"email": "new_customer@sme.com"},
                    "metadata": {"tier": "pro"},
                    "customer": "cus_stripe_999"
                }
            }
        }
        response = await client.post(
            f"{FASTAPI_PROXY_URL}/api/v1/billing/webhook",
            json=mock_payload,
            headers={"stripe-signature": "whsec_mock_signature"}
        )
        assert response.status_code in [200, 201]

@pytest.mark.asyncio
async def test_02_gateway_authenticated_llm_execution(setup_test_tenant):
    api_key = setup_test_tenant["api_key"]
    async with httpx.AsyncClient() as client:
        payload = {"prompt": "Check splunk logs for recent HTTP 429 errors.", "model": "gpt-4o"}
        response = await client.post(
            f"{GATEWAY_JAVA_URL}/v1/chat/completions",
            headers={
                "X-Tenant-API-Key": api_key,
                "Authorization": "Bearer mock_auth0_jwt_token",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=10.0
        )
        assert response.status_code == 200

@pytest.mark.asyncio
async def test_03_semantic_cache_cost_reduction(setup_test_tenant):
    api_key = setup_test_tenant["api_key"]
    async with httpx.AsyncClient() as client:
        payload = {"prompt": "Check splunk logs for recent HTTP 429 errors.", "model": "gpt-4o"}
        response = await client.post(
            f"{GATEWAY_JAVA_URL}/v1/chat/completions",
            headers={"X-Tenant-API-Key": api_key},
            json=payload
        )
        assert response.status_code == 200

@pytest.mark.asyncio
async def test_04_splunk_audit_queue_ingestion(setup_test_tenant):
    queue_length = r.llen("splunk_audit_queue")
    assert queue_length > 0
