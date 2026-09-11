import os
import json
import pytest
import httpx
import redis
import secrets

# Service URLs from docker-compose setup
GATEWAY_JAVA_URL = "http://localhost:8080"
FASTAPI_PROXY_URL = "http://localhost:8000"
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")

r = redis.Redis(host=REDIS_HOST, port=6379, db=0, decode_responses=True)

@pytest.fixture(scope="module")
def setup_test_tenant():
    """Simulates Stripe Webhook provisioning a new 'Pro' tier tenant in Redis."""
    test_email = f"test_owner_{secrets.token_hex(4)}@enterprise.com"
    api_key = f"ak_live_{secrets.token_urlsafe(24)}"
    tenant_id = f"tenant_{secrets.token_hex(6)}"

    tenant_payload = {
        "tenant_id": tenant_id,
        "email": test_email,
        "tier": "pro",
        "rate_limit_rpm": 100,
        "daily_budget_usd": 75.0,
        "stripe_customer_id": "cus_test_mock_123"
    }

    # Store provisioned keys in Redis
    r.set(f"tenant:key:{api_key}", json.dumps(tenant_payload))
    
    yield {"api_key": api_key, "tenant_id": tenant_id, "email": test_email}

    # Cleanup Redis after test completion
    r.delete(f"tenant:key:{api_key}")


@pytest.mark.asyncio
async def test_01_stripe_webhook_provisioning():
    """Step 1: Test simulated Stripe checkout webhook provisions keys correctly in Redis."""
    async with httpx.AsyncClient() as client:
        # Mock Stripe Signature & Webhook Event
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
        assert response.status_code == 200
        assert response.json()["status"] == "success"


@pytest.mark.asyncio
async def test_02_gateway_authenticated_llm_execution(setup_test_tenant):
    """Step 2: Execute LLM Request via Spring Cloud Gateway using provisioned API Key."""
    api_key = setup_test_tenant["api_key"]

    async with httpx.AsyncClient() as client:
        payload = {
            "prompt": "Check splunk logs for recent HTTP 429 errors.",
            "model": "gpt-4o"
        }
        
        # Route through Spring Cloud Gateway (8080) -> FastAPI Proxy (8000)
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
        data = response.json()
        assert data["tenant_id"] == setup_test_tenant["tenant_id"]
        assert "response" in data


@pytest.mark.asyncio
async def test_03_semantic_cache_cost_reduction(setup_test_tenant):
    """Step 3: Repeat identical request to verify Redis Semantic Cache returns $0.00 cost."""
    api_key = setup_test_tenant["api_key"]

    async with httpx.AsyncClient() as client:
        payload = {
            "prompt": "Check splunk logs for recent HTTP 429 errors.",
            "model": "gpt-4o"
        }
        
        response = await client.post(
            f"{GATEWAY_JAVA_URL}/v1/chat/completions",
            headers={"X-Tenant-API-Key": api_key},
            json=payload
        )

        assert response.status_code == 200
        data = response.json()
        # Verify cache hit
        assert data["source"] == "semantic_cache"
        assert data["cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_04_splunk_audit_queue_ingestion(setup_test_tenant):
    """Step 4: Verify audit metrics were pushed to Redis queue for Splunk HEC Exporter."""
    queue_length = r.llen("splunk_audit_queue")
    assert queue_length > 0

    latest_log = json.loads(r.rpop("splunk_audit_queue"))
    assert latest_log["tenant_id"] == setup_test_tenant["tenant_id"]
    assert "cost" in latest_log