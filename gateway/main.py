"""FastAPI gateway with tenant rate limits, budgets, caching, and audit events."""

import hashlib
import json
import time
from typing import Annotated

import redis
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from config.settings import settings
from gateway.auth import verify_jwt

app = FastAPI(title="AgentShield Enterprise API Gateway", version="1.0.0")
r = redis.Redis.from_url(settings.redis_url, decode_responses=True)

API_KEY_NAME = "X-Tenant-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)

TENANT_CONFIG = {
    "key_alpha_123": {"tenant_id": "tenant_alpha", "rate_limit_rpm": 60, "daily_budget_usd": 50.0},
    "key_beta_456": {"tenant_id": "tenant_beta", "rate_limit_rpm": 5, "daily_budget_usd": 2.0},
}


class LLMRequest(BaseModel):
    prompt: str
    model: str = "gpt-4o"
    temperature: float = 0.7


def verify_rate_limit_and_auth(api_key: Annotated[str, Depends(api_key_header)]) -> dict[str, object]:
    tenant = TENANT_CONFIG.get(api_key)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Invalid API Key")

    tenant_id = str(tenant["tenant_id"])
    rate_key = f"rate:{tenant_id}:{int(time.time() // 60)}"
    requests_made = r.incr(rate_key)
    if requests_made == 1:
        r.expire(rate_key, 60)
    if requests_made > int(tenant["rate_limit_rpm"]):
        raise HTTPException(status_code=429, detail="Tenant rate limit exceeded")
    return tenant


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "gateway"}


@app.get("/api/v1/protected")
async def protected_route(token_payload: dict[str, object] = Depends(verify_jwt)) -> dict[str, object]:
    return {"status": "authenticated", "user": token_payload.get("sub")}


@app.post("/v1/chat/completions")
async def process_llm_request(
    payload: LLMRequest,
    tenant_info: Annotated[dict[str, object], Depends(verify_rate_limit_and_auth)],
) -> dict[str, object]:
    tenant_id = str(tenant_info["tenant_id"])
    cache_input = f"{tenant_id}:{payload.model}:{payload.temperature}:{payload.prompt}"
    cache_key = f"cache:{hashlib.sha256(cache_input.encode()).hexdigest()}"
    cached_response = r.get(cache_key)
    if cached_response:
        return {
            "source": "semantic_cache",
            "cost_usd": 0.0,
            "tenant_id": tenant_id,
            "response": json.loads(cached_response),
        }

    calculated_cost = 0.003
    budget_key = f"budget:{tenant_id}:{time.strftime('%Y-%m-%d', time.gmtime())}"
    current_spend = float(r.get(budget_key) or 0.0)
    if current_spend + calculated_cost > float(tenant_info["daily_budget_usd"]):
        raise HTTPException(status_code=402, detail="Tenant daily budget exceeded")

    mock_llm_response = {
        "text": f"Processed query: '{payload.prompt}'",
        "tokens_used": 150,
    }
    r.incrbyfloat(budget_key, calculated_cost)
    r.expire(budget_key, 86400)
    r.setex(cache_key, 3600, json.dumps(mock_llm_response))
    r.lpush(
        "splunk_audit_queue",
        json.dumps(
            {
                "tenant_id": tenant_id,
                "prompt": payload.prompt,
                "tokens": mock_llm_response["tokens_used"],
                "cost": calculated_cost,
                "timestamp": time.time(),
            }
        ),
    )

    return {
        "source": "llm_execution",
        "cost_usd": calculated_cost,
        "tenant_id": tenant_id,
        "response": mock_llm_response,
    }
