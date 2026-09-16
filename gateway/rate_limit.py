"""Tenant token bucket rate limiting module for AgentShield Gateway."""

import asyncio
import math
import os
import time
from typing import Optional, Tuple
import redis
from fastapi import HTTPException, Request, Response, status

from config.settings import settings
from gateway.telemetry import log_telemetry

DEFAULT_TENANT_RPM = 60

r = redis.Redis.from_url(settings.redis_url, decode_responses=True)


def get_redis_client() -> redis.Redis:
    return r


def check_token_bucket(
    r_client: redis.Redis,
    tenant_id: str,
    capacity: int = DEFAULT_TENANT_RPM,
    cost: float = 1.0,
    now: Optional[float] = None,
) -> Tuple[bool, int, int, int, int]:
    """
    Evaluates token bucket capacity for tenant in Redis.
    Key format: rate_limit:{tenant_id}
    Returns: (allowed, remaining_tokens, limit, reset_seconds, retry_after_seconds)
    """
    if now is None:
        now = time.time()

    key = f"rate_limit:{tenant_id}"
    refill_rate = capacity / 60.0  # tokens per second

    try:
        data = r_client.hgetall(key)
    except Exception:
        # If Redis interaction fails, allow request safely
        return True, capacity - 1, capacity, 0, 0

    if not data:
        tokens = float(capacity)
        last_updated = now
    else:
        tokens = float(data.get("tokens", capacity))
        last_updated = float(data.get("last_updated", now))
        elapsed = max(0.0, now - last_updated)
        tokens = min(float(capacity), tokens + elapsed * refill_rate)

    if tokens >= cost:
        remaining_tokens = tokens - cost
        try:
            r_client.hset(
                key,
                mapping={
                    "tokens": str(remaining_tokens),
                    "last_updated": str(now),
                },
            )
            r_client.expire(key, 3600)
        except Exception:
            pass

        remaining_int = int(remaining_tokens)
        reset_seconds = (
            math.ceil((capacity - remaining_tokens) / refill_rate)
            if refill_rate > 0
            else 0
        )
        return True, remaining_int, capacity, reset_seconds, 0
    else:
        needed = cost - tokens
        retry_after = (
            math.ceil(needed / refill_rate) if refill_rate > 0 else 1
        )
        reset_seconds = (
            math.ceil((capacity - tokens) / refill_rate)
            if refill_rate > 0
            else 0
        )
        return False, 0, capacity, reset_seconds, retry_after


def extract_tenant_id(request: Request) -> Optional[str]:
    """Extract tenant ID from request state, API key header, or tenant header."""
    state = getattr(request, "state", None)
    if state and getattr(state, "tenant_id", None):
        return str(state.tenant_id)

    api_key = request.headers.get("X-Tenant-API-Key")
    if api_key:
        from gateway.main import TENANT_CONFIG

        if api_key in TENANT_CONFIG:
            return str(TENANT_CONFIG[api_key]["tenant_id"])

    tenant_header = request.headers.get("X-Tenant-ID")
    if tenant_header:
        return str(tenant_header)

    # In dev mode, default fallback tenant for rate limit dependency evaluation if unpopulated
    if os.getenv("DEV_MODE") == "true":
        return "tenant_alpha"

    return None


async def verify_rate_limit(request: Request, response: Response) -> Optional[str]:
    """
    FastAPI dependency for perimeter token bucket rate limiting on /v1/* endpoints.
    Enforces tenant token bucket, sets rate limit response headers,
    emits telemetry on rate limit exhaustion, and returns HTTP 429 when limits are exceeded.
    """
    path = request.url.path

    # AC-5: Public/unauthenticated endpoints bypass rate limit evaluation entirely
    if (
        not (path.startswith("/v1") or path.startswith("/api/v1"))
        or path in ("/health", "/metrics", "/docs", "/openapi.json", "/redoc")
    ):
        return None

    tenant_id = extract_tenant_id(request)
    if not tenant_id:
        # If tenant ID cannot be determined yet, skip rate limit and defer to auth handler
        return None

    from gateway.main import TENANT_CONFIG

    tenant_info = next(
        (v for k, v in TENANT_CONFIG.items() if v.get("tenant_id") == tenant_id),
        None,
    )
    capacity = (
        int(tenant_info["rate_limit_rpm"])
        if tenant_info and "rate_limit_rpm" in tenant_info
        else DEFAULT_TENANT_RPM
    )

    r_client = get_redis_client()
    allowed, remaining, limit, reset, retry_after = check_token_bucket(
        r_client, tenant_id, capacity=capacity
    )

    if allowed:
        # AC-2: Set rate limit headers
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset)
        return tenant_id
    else:
        # AC-4: Structured telemetry warning on limit exhaustion
        trace_id = "unknown"
        traceparent = request.headers.get("traceparent")
        if traceparent and "-" in traceparent:
            trace_id = traceparent.split("-")[1]

        user_id = getattr(request.state, "user_id", "unknown")

        asyncio.create_task(
            log_telemetry(
                tenant_id=tenant_id,
                message=f"event=\"rate_limit_exceeded\" Rate limit exceeded for tenant {tenant_id} (user {user_id}) on endpoint {path}",
                trace_id=trace_id,
                level="WARN",
                category="RATE_LIMIT_EXCEEDED",
            )
        )

        # AC-3: HTTP 429 response with Retry-After header
        headers = {
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(reset),
        }
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Tenant rate limit exceeded",
            headers=headers,
        )
