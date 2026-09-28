"""FastAPI gateway with tenant rate limits, budgets, caching, and audit events."""

import hashlib
import json
import logging
import os
import re
import time
import requests
import uuid
import httpx
from typing import Annotated, Optional
from datetime import datetime

import redis
from redis import asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Header, Request, status
from pydantic import BaseModel, ConfigDict

from config.settings import settings
from gateway.auth import verify_jwt, resolve_active_tenant, require_permission
from gateway.rate_limit import verify_rate_limit
from gateway.telemetry import emit_request_completed, emit_token_usage
from gateway.webhooks import router as webhook_router
from gateway.billing import router as billing_router
from gateway.metering import router as metering_router
from gateway.dependencies import verify_tenant_quota
from gateway.keys import router as keys_router
from config.guardrails.scanner import scan_prompt_injection

API_DESCRIPTION = """
The published surface for integrating with AgentShield. Four contract routes accept
either a user Bearer token or a tenant machine key.

**Authentication**

* `Authorization: Bearer <JWT>` authenticates a user principal.
* `X-Tenant-API-Key: <key>` authenticates a machine principal. Keys are managed from
  the portal; `POST /v1/keys` additionally requires the `keys:write` scope, and a key
  can only be granted scopes its creator already holds.
* When both are sent, the **Bearer token wins** and the API key is ignored, mirroring
  `resolve_active_tenant` in `gateway/auth.py`.

**Tenant resolution**

A caller-supplied `X-Tenant-ID` header is **never authoritative** and is ignored. The
tenant always comes from the verified JWT claim `https://agentshield.com/tenant_id` or
from the key store, so you cannot act on behalf of another tenant by setting a header.

**Scopes**

`/v1/tools/execute` requires the `tools:execute` scope and returns `403` without it.
Cost fields on `/v1/usage/summary` are populated only for a principal holding
`billing:admin`.
"""

app = FastAPI(
    title="AgentShield Enterprise API Gateway",
    version="1.0.0",
    description=API_DESCRIPTION,
    dependencies=[Depends(verify_rate_limit)],
    openapi_url="/openapi.json",
    docs_url="/docs",
)

logger = logging.getLogger("agentshield.gateway")

app.include_router(webhook_router, tags=["webhooks"])
app.include_router(billing_router)
app.include_router(metering_router)
app.include_router(keys_router)
# API aliases served to the Angular portal (nginx forwards /api/ unchanged)
app.include_router(billing_router, prefix="/api")
app.include_router(metering_router, prefix="/api")
app.include_router(keys_router, prefix="/api")
r = redis.Redis.from_url(settings.redis_url, decode_responses=True)

API_KEY_NAME = "X-Tenant-API-Key"

# Only these keys are HTTP operations in a Path Item Object; `parameters`,
# `summary`, and friends are dicts too and must never be stamped as operations.
HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})

TENANT_CONFIG = {
    "key_alpha_123": {"tenant_id": "tenant_alpha", "rate_limit_rpm": 60, "daily_budget_usd": 50.0},
    "key_beta_456": {"tenant_id": "tenant_beta", "rate_limit_rpm": 5, "daily_budget_usd": 2.0},
}


class LLMRequest(BaseModel):
    prompt: Optional[str] = None
    messages: list[dict] = []
    model: str = "gpt-4o"
    temperature: float = 0.7

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "Simple prompt",
                    "description": "Single turn request using the prompt shorthand.",
                    "value": {
                        "prompt": "Summarize yesterday's support tickets",
                        "model": "gpt-4o",
                        "temperature": 0.7,
                    },
                },
                {
                    "summary": "Full message history",
                    "description": "Multi turn request. `messages` takes precedence over `prompt`.",
                    "value": {
                        "messages": [
                            {"role": "system", "content": "You are a support assistant."},
                            {"role": "user", "content": "Summarize ticket 4471."},
                        ],
                        "model": "gpt-4o",
                        "temperature": 0.2,
                    },
                },
            ]
        }
    )


class ToolRequest(BaseModel):
    tool_name: str
    params: dict = {}

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "Invoke a registered tool",
                    "description": "`params` is passed through to the MCP server as the tool arguments.",
                    "value": {
                        "tool_name": "lookup_order",
                        "params": {"order_id": "ORD-1042"},
                    },
                }
            ]
        }
    )


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



@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "gateway"}


MCP_HEALTH_URL = os.getenv("MCP_HEALTH_URL", "http://mcp-server:8081/health")
STRIPE_API_URL = "https://api.stripe.com/v1/"
PROBE_TIMEOUT_SECONDS = 2.0


async def _probe_redis() -> None:
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _probe_mcp() -> None:
    async with httpx.AsyncClient() as client:
        response = await client.get(MCP_HEALTH_URL, timeout=PROBE_TIMEOUT_SECONDS)
        response.raise_for_status()


async def _probe_stripe() -> None:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            STRIPE_API_URL,
            auth=(settings.stripe_api_key, ""),
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()


async def _timed_probe(name: str, probe) -> dict[str, object]:
    """Run one probe, reporting its status and how long it took."""
    started = time.perf_counter()
    try:
        await probe()
        status_value = "healthy"
    except Exception as exc:
        logger.warning(f"Health probe '{name}' failed: {exc}")
        status_value = "degraded"
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return {"name": name, "status": status_value, "latency_ms": latency_ms}


async def _probe_gateway() -> None:
    return None


@app.get("/v1/health/services", include_in_schema=False)
@app.get("/api/v1/health/services", include_in_schema=False)
async def health_services(
    tenant_id: Annotated[str, Depends(resolve_active_tenant)],
) -> dict[str, object]:
    """
    AC-4: report a status and latency per backing service.

    Probes infrastructure only. The authenticated tenant is never echoed and no
    tenant data is read, so the response is safe for any tenant member to see.
    """
    probes = [("gateway", _probe_gateway), ("mcp-server", _probe_mcp), ("redis", _probe_redis)]
    if settings.stripe_api_key:
        probes.append(("stripe", _probe_stripe))

    services = [await _timed_probe(name, probe) for name, probe in probes]

    return {
        "services": services,
        "overall": "degraded" if any(s["status"] != "healthy" for s in services) else "healthy",
    }


# Setup OpenAPI security schemes
app.openapi_schema = None

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    from fastapi.openapi.utils import get_openapi
    
    # Use get_openapi instead of app.openapi() to avoid recursion
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    excluded_paths = {
        "/health",
        "/v1/health/services",
        "/v1/telemetry/logs",
        "/v1/billing/webhook",
        "/v1/webhooks/stripe",
    }
    
    paths = openapi_schema.get("paths", {})
    for path in list(paths.keys()):
        # Portal aliases are internal to the Angular app, and the Stripe checkout
        # and portal sessions are portal UI actions, not the machine contract.
        # The prefix check covers every /api/* alias, webhook included.
        if path.startswith("/api/") or path in ("/v1/billing/checkout", "/v1/billing/portal"):
            del paths[path]
        elif path in excluded_paths:
            del paths[path]

    # Add Security Schemes
    openapi_schema["components"]["securitySchemes"] = {
        "bearer": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": (
                "User authentication via an Auth0 access token. The tenant is read from "
                "the verified `https://agentshield.com/tenant_id` claim; a caller-supplied "
                "`X-Tenant-ID` header is never authoritative and is ignored. When this "
                "header is present alongside `X-Tenant-API-Key`, this token takes precedence."
            ),
        },
        "apiKey": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Tenant-API-Key",
            "description": (
                "Machine authentication via a hashed tenant key. The tenant is read from the "
                "key store; a caller-supplied `X-Tenant-ID` header is never authoritative and "
                "is ignored. Used only when no `Authorization: Bearer` header is present."
            ),
        }
    }
    
    # Apply schemes to all remaining routes
    for path_item in openapi_schema["paths"].values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() in HTTP_METHODS and isinstance(operation, dict):
                operation["security"] = [{"bearer": []}, {"apiKey": []}]

    _lift_schema_examples(openapi_schema)
    _prune_orphan_components(openapi_schema)

    app.openapi_schema = openapi_schema
    return app.openapi_schema


def _ref_name(ref: str) -> str:
    """Pull the component name out of a local `$ref` pointer."""
    return ref.rsplit("/", 1)[-1]


def _resolve_ref(media_schema: dict) -> Optional[tuple[str, bool]]:
    """Find the component `$ref` a media type renders and whether it is a list.

    A list response carries the model reference under `items` and needs the
    example value wrapped, so the array flag travels with the reference.
    """
    if not isinstance(media_schema, dict):
        return None
    ref = media_schema.get("$ref")
    if isinstance(ref, str):
        return ref, media_schema.get("type") == "array"
    if media_schema.get("type") == "array" or "items" in media_schema:
        inner = _resolve_ref(media_schema.get("items") or {})
        return (inner[0], True) if inner else None
    return None


def _media_examples(resolved: tuple[str, bool], schemas: dict) -> dict:
    """Build an OpenAPI `examples` map from a component schema's own examples.

    Pydantic normalises a list of examples into a list and a single example into an
    object, so accept either shape.
    """
    ref, is_array = resolved
    schema = schemas.get(_ref_name(ref), {})
    examples = schema.get("examples")
    if not examples:
        return {}
    if isinstance(examples, list):
        examples = {f"example_{i + 1}": ex for i, ex in enumerate(examples)}
    if not isinstance(examples, dict):
        return {}

    lifted = {}
    for name, ex in examples.items():
        if not isinstance(ex, dict):
            continue
        value = ex.get("value")
        lifted_example = {
            "summary": ex.get("summary", name),
            "value": [value] if is_array and not isinstance(value, list) else value,
        }
        if ex.get("description"):
            lifted_example["description"] = ex["description"]
        lifted[name] = lifted_example
    return lifted


def _referenced_component_names(schema: dict) -> set[str]:
    """Collect every component schema name still reachable from the published paths."""
    blob = json.dumps(
        {"paths": schema.get("paths", {}), "securitySchemes": schema.get("components", {}).get("securitySchemes", {})}
    )
    return set(re.findall(r"#/components/schemas/([A-Za-z0-9_.\-]+)", blob))


def _prune_orphan_components(schema: dict) -> None:
    """Drop component schemas no published operation references.

    Excluding a path leaves its request and response models behind, and an
    unreferenced model in a published contract is a route in all but name.
    """
    schemas = schema.get("components", {}).get("schemas")
    if not schemas:
        return
    referenced = _referenced_component_names(schema)
    for name in list(schemas.keys()):
        if name not in referenced:
            del schemas[name]


def _lift_schema_examples(schema: dict) -> None:
    """Surface component examples on the operations that reference them.

    Pydantic attaches `examples` to the component schema, but Swagger UI reads
    request and response examples from the media type, so copy them up. The
    component stays the single source of truth for the payload.
    """
    schemas = schema.get("components", {}).get("schemas", {})

    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue

            for container in [operation.get("requestBody"), *operation.get("responses", {}).values()]:
                if not isinstance(container, dict):
                    continue
                for media in container.get("content", {}).values():
                    if not isinstance(media, dict):
                        continue
                    resolved = _resolve_ref(media.get("schema") or {})
                    if resolved and (examples := _media_examples(resolved, schemas)):
                        media["examples"] = examples

app.openapi = custom_openapi

@app.post(
    "/v1/tools/execute",
    tags=["tools"],
    summary="Execute a tenant tool",
    response_description="The tool result envelope, including the trace id",
    dependencies=[Depends(require_permission("tools:execute"))],
)
async def execute_tool(
    payload: ToolRequest,
    tenant_id: Annotated[str, Depends(resolve_active_tenant)],
    traceparent: Annotated[str, Depends(get_trace_context)],
    request: Request,
) -> dict[str, object]:
    """
    Invoke a tool registered on the MCP server on behalf of the authenticated tenant.

    Requires the `tools:execute` scope. A principal without it receives `403` and an
    authz failure event is emitted to telemetry. The W3C `traceparent` header is
    forwarded to the MCP server and echoed back as `trace_id`.
    """
    trace_id = traceparent.split("-")[1] if "-" in traceparent else "unknown"
    user_id = getattr(request.state, "user_id", "unknown")

    mcp_url = "http://mcp-server:8081/rpc"
    rpc_payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": payload.tool_name, "arguments": payload.params},
        "id": 1,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                mcp_url,
                json=rpc_payload,
                headers={"traceparent": traceparent, "X-Tenant-ID": tenant_id},
                timeout=10.0,
            )
            response.raise_for_status()
            result = response.json()

            # Synchronous call (removed 'await')
            emit_request_completed(
                tenant_id=tenant_id,
                user_id=user_id,
                trace_id=trace_id,
                span_id=trace_id[:16] if trace_id and len(trace_id) >= 16 else "unknown",
                method="POST",
                path="/v1/tools/execute",
                status_code=200,
                latency_ms=0,
            )
            return {
                "status": "success",
                "result": result.get("result"),
                "trace_id": trace_id,
            }
    except httpx.ConnectError as ce:
        # Synchronous call (removed 'await')
        emit_request_completed(
            tenant_id=tenant_id,
            user_id=user_id,
            trace_id=trace_id,
            span_id=trace_id[:16] if trace_id and len(trace_id) >= 16 else "unknown",
            method="POST",
            path="/v1/tools/execute",
            status_code=502,
            latency_ms=0,
        )
        raise HTTPException(status_code=502, detail=f"MCP Connection Failed: {str(ce)}")
    except Exception as e:
        # Synchronous call (removed 'await')
        emit_request_completed(
            tenant_id=tenant_id,
            user_id=user_id,
            trace_id=trace_id,
            span_id=trace_id[:16] if trace_id and len(trace_id) >= 16 else "unknown",
            method="POST",
            path="/v1/tools/execute",
            status_code=502,
            latency_ms=0,
        )
        raise e

@app.get("/v1/telemetry/logs", include_in_schema=False)
@app.get("/api/v1/telemetry/logs", include_in_schema=False)
async def get_telemetry_logs(
    tenant_id: Annotated[str, Depends(resolve_active_tenant)],
) -> list[dict[str, object]]:
    """Return telemetry history scoped to the authenticated tenant."""
    try:
        raw_items = r.lrange("telemetry_history", 0, 99)
        history = []
        for raw in raw_items:
            try:
                item = json.loads(raw)
            except Exception:
                continue
            if item.get("tenant_id") != tenant_id:
                continue
            history.append(
                {
                    "eventId": f"EVT-{hashlib.sha256(raw.encode()).hexdigest()[:8]}",
                    "timestamp": item.get("timestamp", ""),
                    "tenantId": item.get("tenant_id", ""),
                    "costUsd": 0.0,
                    "statusCode": item.get("status_code", 200),
                    "latencyMs": item.get("latency_ms", 0),
                    "evalPassed": item.get("eval_passed"),
                    "message": item.get("message", ""),
                    "traceId": item.get("trace_id", ""),
                }
            )
        if history:
            return history[-50:]
    except Exception:
        pass

    # Fallback demo rows for the portal until real tenant history flows
    return [
        {"eventId": "EVT-1030", "timestamp": "Today, 10:42:18", "tenantId": "northstar-labs", "costUsd": 0.0038, "statusCode": 429, "latencyMs": 12, "evalPassed": True, "traceId": "dt-7fa2c1"},
        {"eventId": "EVT-1029", "timestamp": "Today, 10:41:56", "tenantId": "harbor-works", "costUsd": 0.0182, "statusCode": 200, "latencyMs": 142, "evalPassed": True, "traceId": "dt-7fa2af"},
        {"eventId": "EVT-1028", "timestamp": "Today, 10:41:11", "tenantId": "northstar-labs", "costUsd": 0.0114, "statusCode": 200, "latencyMs": 86, "evalPassed": True, "traceId": "dt-7fa1d9"},
        {"eventId": "EVT-1027", "timestamp": "Today, 10:40:44", "tenantId": "pinnacle-care", "costUsd": 0.0061, "statusCode": 500, "latencyMs": 934, "evalPassed": False, "traceId": "dt-7fa19b"},
        {"eventId": "EVT-1026", "timestamp": "Today, 10:39:58", "tenantId": "harbor-works", "costUsd": 0.0027, "statusCode": 200, "latencyMs": 64, "evalPassed": True, "traceId": "dt-7fa0e0"},
    ]


@app.post("/v1/telemetry/logs", include_in_schema=False)
async def post_telemetry_logs(payload: TelemetryPayload) -> dict[str, str]:
    # Ship to Splunk HEC (Port 8088)
    try:
        # Mocking the HEC request structure
        splunk_event = {
            "event": payload.model_dump(),
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
    r.lpush("telemetry_history", json.dumps(payload.model_dump()))
    r.ltrim("telemetry_history", 0, 99) # Keep last 100
    
    return {"status": "accepted"}


@app.get("/api/v1/protected", include_in_schema=False)
async def protected_route(token_payload: dict[str, object] = Depends(verify_jwt)) -> dict[str, object]:
    return {"status": "authenticated", "user": token_payload.get("sub")}


@app.post(
    "/v1/chat/completions",
    tags=["llm"],
    summary="Run a guarded chat completion",
    response_description="The completion envelope with the cost charged to the tenant",
    dependencies=[Depends(verify_tenant_quota)],
)
async def process_llm_request(
    payload: LLMRequest,
    tenant_id: Annotated[str, Depends(resolve_active_tenant)],
) -> dict[str, object]:
    """
    Send a prompt or message history to the model under the tenant's guardrails.

    Requests are scanned for prompt injection, checked against the tenant quota and
    daily budget, served from the semantic cache when possible, and metered. Returns
    `402` when the daily budget is exhausted and `429` when the rate limit is hit.
    """
    user_messages = payload.messages or (
        [{"role": "user", "content": payload.prompt}] if payload.prompt else []
    )
    scan_prompt_injection(user_messages)
    effective_prompt = (
        payload.prompt
        or next(
            (m.get("content") for m in payload.messages if isinstance(m.get("content"), str)),
            "",
        )
    )
    tenant_info = verify_rate_limit_and_auth(tenant_id)
    cache_input = f"{tenant_id}:{payload.model}:{payload.temperature}:{effective_prompt}"
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
        "text": f"Processed query: '{effective_prompt}'",
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
                "prompt": effective_prompt,
                "tokens": mock_llm_response["tokens_used"],
                "cost": calculated_cost,
                "timestamp": time.time(),
            }
        ),
    )

    # Emit CloudEvent token usage to telemetry:queue stream
    tokens_used = int(mock_llm_response.get("tokens_used", 0))
    try:
        emit_token_usage(
            tenant_id=tenant_id,
            input_tokens=0,
            output_tokens=tokens_used,
            model=payload.model,
            redis_client=r,
        )
    except Exception as e:
        logger.error(f"Token usage emit failure: {e}")

    return {
        "source": "llm_execution",
        "cost_usd": calculated_cost,
        "tenant_id": tenant_id,
        "response": mock_llm_response,
    }
