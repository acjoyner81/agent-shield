"""FastAPI gateway with tenant rate limits, budgets, caching, and audit events."""

import hashlib
import json
import time
import requests
import uuid
import httpx
from typing import Annotated, Optional
from datetime import datetime

import redis
from fastapi import Depends, FastAPI, HTTPException, Header, Request, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from config.settings import settings
from gateway.auth import verify_jwt, get_verified_tenant, require_permission
from gateway.rate_limit import verify_rate_limit
from gateway.telemetry import log_telemetry
from gateway.webhooks import router as webhook_router


app = FastAPI(
    title="AgentShield Enterprise API Gateway",
    version="1.0.0",
    dependencies=[Depends(verify_rate_limit)],
)

app.include_router(webhook_router, tags=["webhooks"])
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


class ToolRequest(BaseModel):
    tool_name: str
    params: dict = {}


class TelemetryPayload(BaseModel):
    tenant_id: str
    level: str = "INFO"
    message: str
    timestamp: float = None


def verify_rate_limit_and_auth(tenant_id: str) -> dict[str, object]:
    # Find tenant config by ID instead of API key
    tenant = next((v for k, v in TENANT_CONFIG.items() if v["tenant_id"] == tenant_id), None)
    if tenant is None:
        raise HTTPException(status_code=403, detail="Tenant not registered in system")
    return tenant


def get_trace_context(traceparent: Optional[str] = Header(None)) -> str:
    """Ensures a W3C traceparent exists. Generates one if missing."""
    if traceparent and len(traceparent) >= 34:
        return traceparent
    # Format: 00-{trace_id}-{parent_id}-{flags}
    return f"00-{uuid.uuid4().hex}-{uuid.uuid4().hex[:16]}-01"



@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "gateway"}


@app.post("/v1/tools/execute", dependencies=[Depends(require_permission("tools:execute"))])
async def execute_tool(
    payload: ToolRequest,
    tenant_id: Annotated[str, Depends(get_verified_tenant)],
    traceparent: Annotated[str, Depends(get_trace_context)],
    request: Request,
) -> dict[str, object]:
    trace_id = traceparent.split("-")[1] if "-" in traceparent else "unknown"
    user_id = getattr(request.state, "user_id", "unknown")
    
    await log_telemetry(tenant_id, f"Executing tool {payload.tool_name} for user {user_id}", trace_id, category="MCP_ROUTE")
    
    # Forward to MCP Server (JSON-RPC)
    mcp_url = "http://mcp-server:8081/rpc" # Inferred from AGENTS.md
    rpc_payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": payload.tool_name, "arguments": payload.params},
        "id": 1
    }
    
    try:
        async with httpx.AsyncClient() as client:
            print(f"DEBUG: Calling MCP server at {mcp_url} with payload {rpc_payload}")
            response = await client.post(
                mcp_url, 
                json=rpc_payload, 
                headers={"traceparent": traceparent, "X-Tenant-ID": tenant_id},
                timeout=10.0
            )
            print(f"DEBUG: MCP response status: {response.status_code}")
            response.raise_for_status()
            result = response.json()
            
            await log_telemetry(tenant_id, f"Tool {payload.tool_name} returned result", trace_id, category="MCP_ROUTE")
            return {
                "status": "success",
                "result": result.get("result"),
                "trace_id": trace_id
            }
    except httpx.ConnectError as ce:
        await log_telemetry(tenant_id, f"MCP Connection Error: {str(ce)}", trace_id, level="ERROR", category="MCP_ROUTE")
        print(f"DEBUG: Connection Error: {ce}")
        raise HTTPException(status_code=502, detail=f"MCP Connection Failed: {str(ce)}")
    except Exception as e:
        await log_telemetry(tenant_id, f"MCP Execution failed: {str(e)}", trace_id, level="ERROR", category="MCP_ROUTE")
        print(f"DEBUG: General Error: {e}")
        raise HTTPException(status_code=502, detail=f"MCP Server Error: {str(e)}")


@app.get("/v1/telemetry/logs")
async def get_telemetry_logs() -> list[dict[str, object]]:
    # Mock logs to satisfy the frontend portal
    return [
        {"timestamp": time.time() - 100, "level": "INFO", "message": "User authenticated successfully", "tenant_id": "tenant_alpha"},
        {"timestamp": time.time() - 50, "level": "WARN", "message": "Rate limit approaching", "tenant_id": "tenant_alpha"},
        {"timestamp": time.time() - 10, "level": "ERROR", "message": "Upstream LLM timeout", "tenant_id": "tenant_beta"},
    ]


@app.post("/v1/telemetry/logs")
async def post_telemetry_logs(payload: TelemetryPayload) -> dict[str, str]:
    # Ship to Splunk HEC (Port 8088)
    try:
        # Mocking the HEC request structure
        splunk_event = {
            "event": payload.dict(),
            "sourcetype": "agent_shield_telemetry"
        }
        # We use a timeout to prevent the gateway from hanging if Splunk is slow
        requests.post(
            "http://splunk:8088/services/collector", 
            json=splunk_event, 
            timeout=0.5
        )
    except Exception as e:
        print(f"Splunk HEC failure: {e}")

    # Also keep a short-term history in Redis for the GET endpoint to eventually use
    r.lpush("telemetry_history", json.dumps(payload.dict()))
    r.ltrim("telemetry_history", 0, 99) # Keep last 100
    
    return {"status": "accepted"}


@app.post("/v1/billing/checkout")
async def billing_checkout(payload: dict = None) -> dict[str, object]:
    # Mock checkout contract to clear 404s on the portal
    return {
        "status": "success",
        "checkout_url": "https://checkout.stripe.com/mock_session_123",
        "message": "Checkout session created successfully"
    }


@app.get("/api/v1/protected")
async def protected_route(token_payload: dict[str, object] = Depends(verify_jwt)) -> dict[str, object]:
    return {"status": "authenticated", "user": token_payload.get("sub")}


@app.post("/v1/chat/completions")
async def process_llm_request(
    payload: LLMRequest,
    tenant_id: Annotated[str, Depends(get_verified_tenant)],
) -> dict[str, object]:
    tenant_info = verify_rate_limit_and_auth(tenant_id)
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
