"""FastAPI gateway with tenant rate limits, budgets, caching, and audit events."""

import hashlib
import json
import logging
import os
import re
import time
import uuid
import httpx
from typing import Annotated, Optional
from datetime import datetime, timezone

import redis
from redis import asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Header, Query, Request, status
from pydantic import BaseModel, ConfigDict

from config.settings import settings
from gateway.auth import verify_jwt, resolve_active_tenant, require_permission
from gateway.rate_limit import verify_rate_limit
from gateway.pricing import estimate_cost_usd
from gateway.telemetry import (
    HISTORY_LIMIT,
    emit_event,
    emit_request_completed,
    emit_token_usage,
)
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
tenant always comes from the verified JWT claim `https://api.agentshield.local/tenant_id` or
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


# ---------------------------------------------------------------------------
# Splunk
#
# Read only on purpose. A GET to the collector's /health endpoint does not write
# to the index, so polling this surface every minute does not turn the gateway's
# own health check into a source of telemetry.
#
# The CA file is read on every probe instead of captured at import, because
# Splunk publishes it during its boot. A gateway container that starts first must
# report degraded with that reason rather than refusing to start, and must start
# reporting healthy on its own once the file lands.
# ---------------------------------------------------------------------------
SPLUNK_HEC_URL = os.getenv("SPLUNK_HEC_URL", "https://splunk:8088/services/collector/event")
SPLUNK_HEC_TOKEN_ENV = os.getenv("SPLUNK_HEC_TOKEN", "")
SPLUNK_HEC_CA = os.getenv("SPLUNK_HEC_CA", "/etc/agentshield/splunk-ca/ca.crt")

# A ship that was attempted and has not succeeded inside this window means the
# pipeline is stalled, which is the failure mode that hid for three days. The
# file-integrity probe uses the same shape for the same reason.
SPLUNK_SHIP_STALE_SECONDS = int(os.getenv("SPLUNK_HEALTH_MAX_SHIP_AGE_SEC", "300"))

# Written by the aggregator, read here.
SHIP_RECEIPT_KEY = "telemetry:ship"
TELEMETRY_QUEUE_KEY = "telemetry:queue"
DLQ_KEY = "telemetry:dlq"
DLQ_DROPPED_KEY = "telemetry:dlq:dropped"
REDRIVE_LOCK_KEY = "telemetry:redrive:lock"

# Replay is one script for the reason documented on the endpoint: a read then a
# write leaves a window where an interrupted replay has destroyed the entry it
# was moving without having written its replacement.
#
# RPOP matches the RPUSH every dead letter writer uses, so a replay reaches every
# entry. While the writers disagreed on the end, a replay could never drain the
# queue and the trim boundary was undefined.
REDRIVE_LUA = """
local limit = tonumber(ARGV[1])
local moved = 0
while moved < limit do
  local entry = redis.call('RPOP', KEYS[1])
  if not entry then
    break
  end
  redis.call('XADD', KEYS[2], '*', 'payload', entry)
  moved = moved + 1
end
return moved
"""

# Only the holder releases, so a replay that ran past the lock's expiry cannot
# clear a lock a later replay now owns.
RELEASE_LOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def _epoch_seconds(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _probe_splunk() -> None:
    """Report whether telemetry is actually reaching Splunk.

    Three separate things are checked, and each names itself in the reason:

    1. the collector answers on TLS, verified against the published authority
    2. a ship that was attempted has succeeded recently enough
    3. events are queued but nothing has ever shipped

    Without the third, a cold start reports healthy while silently discarding
    every event, which is the state this whole spec exists to end.
    """
    if not SPLUNK_HEC_TOKEN_ENV:
        raise RuntimeError("SPLUNK_HEC_TOKEN is not configured, so shipping cannot be attempted")

    if not os.path.exists(SPLUNK_HEC_CA):
        # Not fatal: Splunk publishes its authority minutes into boot, and the
        # gateway usually starts first. The reason is actionable, which is the
        # whole point of carrying it.
        raise RuntimeError(
            f"the Splunk certificate authority {SPLUNK_HEC_CA} is not published yet"
        )

    # The reachability check reads the collector's own health endpoint rather
    # than posting an empty batch to /event: the collector answers an empty
    # post with 400, so that probe could only ever degrade, and a probe that
    # sends a synthetic event to prove delivery would pollute the very index
    # it is proving. TLS is still verified against the published authority, and
    # authenticated delivery is proven by the receipts below, which only a
    # successful ship writes.
    health_url = SPLUNK_HEC_URL.removesuffix("/event") + "/health"
    async with httpx.AsyncClient(verify=SPLUNK_HEC_CA) as client:
        response = await client.get(health_url, timeout=PROBE_TIMEOUT_SECONDS)
        response.raise_for_status()

    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        receipt = await client.hgetall(SHIP_RECEIPT_KEY)
        now = time.time()

        last_attempt = _epoch_seconds(receipt.get("last_attempt"))
        if last_attempt is not None:
            age = now - last_attempt
            if age > SPLUNK_SHIP_STALE_SECONDS:
                failures = receipt.get("consecutive_failures") or "?"
                reason = receipt.get("last_error") or "no reason recorded"
                raise RuntimeError(
                    f"the last ship attempt is {int(age)}s old, over the "
                    f"{SPLUNK_SHIP_STALE_SECONDS}s limit ({failures} consecutive "
                    f"failures): {reason}"
                )

        last_success = _epoch_seconds(receipt.get("last_success"))
        if last_success is None:
            queued = await client.xlen(TELEMETRY_QUEUE_KEY)
            if queued:
                raise RuntimeError(
                    f"{queued} events are queued and no ship has ever succeeded"
                )
    finally:
        await client.aclose()


async def _timed_probe(name: str, probe) -> dict[str, object]:
    """Run one probe, reporting its status and how long it took.

    The reason travels with the status. A service list that reports only
    "degraded" tells an operator nothing they can act on, and the reason is
    already known here, so it is returned rather than only logged.
    """
    started = time.perf_counter()
    detail = None
    try:
        await probe()
        status_value = "healthy"
    except Exception as exc:
        logger.warning(f"Health probe '{name}' failed: {exc}")
        status_value = "degraded"
        detail = str(exc) or exc.__class__.__name__
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "name": name,
        "status": status_value,
        "latency_ms": latency_ms,
        "detail": detail,
    }


async def _probe_gateway() -> None:
    return None


# Tripwire's verdict path, on a volume the gateway mounts read-only. The monitor
# rewrites this after every check cycle.
INTEGRITY_VERDICT_PATH = "/var/lib/tripwire/status/verdict.json"

# A verdict older than this means the monitor is not reporting. Two check cycles
# at the default interval of 300s leaves room for one slow run without calling a
# live monitor dead. Treating a stale or absent verdict as degraded is the whole
# point: a monitor that silently stopped must never present as a healthy one,
# which is the same lie as a service that has died reporting green.
INTEGRITY_STALE_SECONDS = 900


async def _probe_file_integrity() -> None:
    """Fail unless Tripwire is reporting a fresh clean verdict."""
    try:
        with open(INTEGRITY_VERDICT_PATH, "r", encoding="utf-8") as handle:
            verdict = json.load(handle)
    except FileNotFoundError as exc:
        raise RuntimeError("no Tripwire verdict published yet") from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Tripwire verdict unreadable: {exc}") from exc

    checked_at = str(verdict.get("checked_at", ""))
    age = _verdict_age_seconds(checked_at)
    if age is None:
        raise RuntimeError(f"Tripwire verdict has no usable timestamp: {checked_at!r}")
    if age > INTEGRITY_STALE_SECONDS:
        raise RuntimeError(f"Tripwire verdict is {int(age)}s old, over the {INTEGRITY_STALE_SECONDS}s limit")

    violations = verdict.get("violations", 0)
    objects = verdict.get("objects", 0)
    if verdict.get("verdict") != "clean" or violations:
        # The monitor supplies a reason for the states a count cannot describe.
        # When the policy no longer matches the baseline the check scans nothing
        # at all, so violations and objects are both zero and "0 of 0 monitored
        # objects differ" would read as a healthy monitor that found nothing,
        # which is the opposite of what happened: nothing was verified.
        reason = str(verdict.get("reason") or "").strip()
        if reason:
            raise RuntimeError(reason)
        raise RuntimeError(
            f"{violations} of {objects} monitored objects differ from the approved baseline"
        )


def _verdict_age_seconds(checked_at: str) -> Optional[float]:
    """Seconds since the verdict was written, or None if it cannot be read.

    Compared against UTC because the monitor writes UTC. Naive local comparison
    would make the verdict look arbitrarily old or new depending on the host
    clock, and the container has no guarantee of a local timezone.
    """
    try:
        parsed = datetime.strptime(checked_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds()


@app.get("/v1/health/services", include_in_schema=False)
@app.get("/api/v1/health/services", include_in_schema=False)
async def health_services(
    request: Request,
    tenant_id: Annotated[str, Depends(resolve_active_tenant)],
) -> dict[str, object]:
    """
    AC-4: report a status and latency per backing service.

    Probes infrastructure only. The authenticated tenant is never echoed and no
    tenant data is read, so the response is safe for any tenant member to see.

    The file-integrity probe reads Tripwire's published verdict from a volume
    mounted read only. That is platform integrity state, which belongs here
    rather than in a tenant's audit stream: the telemetry alert Tripwire also
    sends lands under tenant_id "system" and is read by no human.
    """
    probes = [
        ("gateway", _probe_gateway),
        ("mcp-server", _probe_mcp),
        ("redis", _probe_redis),
        ("file-integrity", _probe_file_integrity),
        ("splunk", _probe_splunk),
    ]
    if settings.stripe_api_key:
        probes.append(("stripe", _probe_stripe))

    services = [await _timed_probe(name, probe) for name, probe in probes]

    response: dict[str, object] = {
        "services": services,
        "overall": "degraded" if any(s["status"] != "healthy" for s in services) else "healthy",
    }

    # Dead letter depth and drop totals are a fleet-wide multi-tenant aggregate,
    # so they sit behind `telemetry:admin` in their own object rather than mixed
    # into the per service entries, which stay safe for any tenant member to see.
    # Read straight off request state rather than through require_permission:
    # that dependency raises 403, and a health check must still answer for a
    # tenant who cannot see these counts.
    if "telemetry:admin" in getattr(request.state, "permissions", set()):
        response["metrics"] = await _telemetry_metrics()

    return response


async def _telemetry_metrics() -> dict[str, int]:
    """Read the dead letter queue's depth and lifetime drop count."""
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        depth = int(await client.llen("telemetry:dlq") or 0)
        dropped = int(await client.get("telemetry:dlq:dropped") or 0)
        return {"dlq_depth": depth, "dlq_dropped": dropped}
    finally:
        await client.aclose()


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
        # Operational, not contract: replaying the dead letter queue is an
        # operator action against platform-wide state, and publishing it would
        # also break the published-schema regression test.
        "/v1/telemetry/redrive",
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
                "the verified `https://api.agentshield.local/tenant_id` claim; a caller-supplied "
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

def _audit_event_id(item: dict, raw: str) -> str:
    """A short, stable id for an audit row.

    Rows written by the gateway carry the CloudEvent's own id, so the value the
    user reads traces back to the event that Splunk received. Rows that predate
    that (the Tripwire ingest path posts a bare payload) fall back to hashing
    the stored text, which is all that is available for them.
    """
    event_id = item.get("event_id")
    if isinstance(event_id, str) and event_id:
        return f"EVT-{event_id.removeprefix('evt_')[:8]}"
    return f"EVT-{hashlib.sha256(raw.encode()).hexdigest()[:8]}"


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
            # Only an LLM call has a cost. A key rotation priced at $0.0000 was a
            # fabricated figure on a row that never spent anything.
            cost_usd = None
            if item.get("model") and item.get("total_tokens") is not None:
                cost_usd = estimate_cost_usd(str(item["model"]), int(item["total_tokens"]))
            history.append(
                {
                    "eventId": _audit_event_id(item, raw),
                    "timestamp": item.get("timestamp", ""),
                    "tenantId": item.get("tenant_id", ""),
                    "costUsd": cost_usd,
                    # Absent means absent. Defaulting to 200/0 claimed every
                    # event was a successful request of zero latency, including
                    # rate limit drops and denied calls.
                    "statusCode": item.get("status_code"),
                    "latencyMs": item.get("latency_ms"),
                    "evalPassed": item.get("eval_passed"),
                    "message": item.get("message", ""),
                    "traceId": item.get("trace_id", ""),
                }
            )
        return history[-50:]
    except Exception:
        logger.exception("telemetry history read failed for tenant %s", tenant_id)

    # An honest empty list. This used to return five hardcoded "demo" rows, which
    # were not merely invented: they carried tenant ids belonging to other
    # tenants, so a tenant with no history was shown another tenant's events.
    return []


@app.post("/v1/telemetry/logs", include_in_schema=False)
async def post_telemetry_logs(
    payload: TelemetryPayload,
    tenant_id: Annotated[str, Depends(resolve_active_tenant)],
) -> dict[str, str]:
    # The tenant comes from the verified credential, never from the body. This
    # route used to declare no auth dependency at all and write
    # `payload.tenant_id` straight into `telemetry_history`, so an anonymous
    # caller could forge audit rows in any tenant's stream and, because the list
    # is trimmed to HISTORY_LIMIT, evict that tenant's real rows.
    payload.tenant_id = tenant_id

    # Enqueue only. This route used to POST to the collector itself, over plain
    # HTTP against a TLS-only port, and threw the result away. So this repository
    # had three call sites into the collector with three different configurations,
    # and the one on the request path was broken in a way that could never be
    # detected: the response body was never inspected, so a rejection looked
    # identical to a delivery. Routing through the shared emitter means one
    # configuration, verified TLS, and an `event_id` the caller can trace through
    # the queue and the dead letter queue.
    #
    # `emit_event` writes the portal's read model too, so the separate lpush and
    # ltrim below are gone: keeping them would put this payload in the history
    # list twice, once as a raw payload and once as a flattened row, and only one
    # of those shapes has the fields the stream renders.
    #
    # `TelemetryPayload.timestamp` is an optional float with no defined unit, so it
    # is not forwarded; the envelope owns the clock.
    payload_json = emit_event(
        tenant_id=tenant_id,
        event_type="agentshield.telemetry.request.completed",
        data={
            "method": "POST",
            "path": "/v1/telemetry/logs",
            "status_code": 202,
            "latency_ms": 0.0,
            "level": payload.level,
            "message": payload.message,
        },
        redis_client=r,
    )

    return {"status": "accepted", "event_id": json.loads(payload_json)["event_id"]}


@app.post("/v1/telemetry/redrive", include_in_schema=False)
async def post_telemetry_redrive(
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    _admin: Annotated[bool, Depends(require_permission("telemetry:admin"))] = True,
) -> dict[str, object]:
    """Replay dead lettered telemetry back onto the queue.

    The move is one Lua script, not a read followed by a write. This project has
    shipped that exact bug once already, in the metering engine: it claimed an
    event and then applied the rollup, and a crash between the two stranded the
    event so its dead letter entry redrove as a duplicate and the usage was lost
    for good. One script makes that window unobservable.

    Returns `moved: 0` with the collector's last error rather than failing while
    Splunk is unreachable, because replaying into a broken collector just refills
    the dead letter queue while reporting progress that never becomes telemetry.
    """
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    lock_token = uuid.uuid4().hex
    try:
        receipt = await client.hgetall(SHIP_RECEIPT_KEY)
        last_error = receipt.get("last_error") or ""
        last_success = _epoch_seconds(receipt.get("last_success"))
        last_attempt = _epoch_seconds(receipt.get("last_attempt"))
        dropped_total = int(await client.get("telemetry:dlq:dropped") or 0)

        async def outcome(moved: int) -> dict[str, object]:
            return {
                "moved": moved,
                "remaining": int(await client.llen("telemetry:dlq") or 0),
                "dropped_total": dropped_total,
                "last_error": last_error,
            }

        # A recorded failure that has not been followed by a success means the
        # collector is still refusing. Replaying now would only move entries from
        # one list to another and spend the queue's capacity doing it.
        unresolved = last_error and (
            last_success is None or (last_attempt is not None and last_attempt > last_success)
        )
        if unresolved:
            return await outcome(0)

        # The lock is not what makes replay safe, the atomic pop is. It exists so
        # a second concurrent replay gets a 409 instead of quietly competing for
        # the same entries and reporting a misleading `moved`. Released explicitly,
        # and the expiry covers a holder that dies mid-replay.
        acquired = await client.set(REDRIVE_LOCK_KEY, lock_token, nx=True, ex=30)
        if not acquired:
            raise HTTPException(
                status_code=409,
                detail="A telemetry replay is already running; retry shortly.",
            )
        try:
            moved = int(
                await client.eval(REDRIVE_LUA, 2, DLQ_KEY, TELEMETRY_QUEUE_KEY, limit)
            )
            return await outcome(moved)
        finally:
            await client.eval(RELEASE_LOCK_LUA, 1, REDRIVE_LOCK_KEY, lock_token)
    finally:
        await client.aclose()


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
    max_budget = float(tenant_info["daily_budget_usd"])
    if current_spend + calculated_cost > max_budget:
        # Same three keys `llm_proxy.completion_proxy` raises (Spec 0013), so both
        # "budget exhausted" paths tell the portal the same story. Both figures are
        # already in scope from the reads above.
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Tenant budget limit exceeded",
                "current_spend_usd": current_spend,
                "max_budget_usd": max_budget,
            },
        )

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
