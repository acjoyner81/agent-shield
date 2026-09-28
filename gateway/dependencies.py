"""Tenant quota enforcement for billable gateways (Spec 0007 / 0009)."""

import json
import os
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from redis import asyncio as aioredis

from gateway.auth import resolve_active_tenant

def require_permission(permission: str):
    """
    Dependency factory that enforces a specific permission scope.
    Returns the permission string if granted, otherwise raises 403 Forbidden.
    """
    async def dependency(request: Request):
        permissions = getattr(request.state, "permissions", None) or set()
        if permission not in permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {permission}",
            )
        return permission
    return dependency

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

TIER_LIMITS = {
    "free": 10000,
    "starter": 100000,
    "pro": 1000000,
    "enterprise": float("inf"),
}


async def _current_billing_period(now=None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


async def _resolve_tier(redis_client, tenant_id: str) -> str:
    """Resolve the tenant's effective tier from stored entitlements, not headers."""
    raw = await redis_client.get(f"tenant:{tenant_id}:tier")
    if raw and raw in TIER_LIMITS:
        return raw

    cached = await redis_client.get(f"tenant:{tenant_id}:entitlements")
    if cached:
        try:
            entitlements = json.loads(cached)
            tier = entitlements.get("tier")
            if tier in TIER_LIMITS:
                return tier
        except Exception:
            pass
    return "free"


async def verify_tenant_quota(
    request: Request,
    tenant_id: Annotated[str, Depends(resolve_active_tenant)],
) -> None:
    """
    Enforce the tenant's tier token allowance for the current period.

    The tenant identity and tier always come from authentication state and
    stored entitlements, never from client supplied headers. This dependency
    only runs after a Bearer token or API key has authenticated the caller.
    """
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        is_over_limit = await redis_client.get(f"tenant:over_limit:{tenant_id}")
        if is_over_limit == "true":
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Tenant {tenant_id} has exceeded their token quota. Please upgrade your plan.",
            )

        period = await _current_billing_period()
        usage_key = f"billing:usage:{tenant_id}:{period}"
        total_tokens_str = await redis_client.hget(usage_key, "total_tokens")
        total_tokens = int(total_tokens_str) if total_tokens_str else 0

        tier = await _resolve_tier(redis_client, tenant_id)
        max_allowed = TIER_LIMITS.get(tier, TIER_LIMITS["free"])

        if total_tokens >= max_allowed:
            await redis_client.set(f"tenant:over_limit:{tenant_id}", "true")
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Token limit reached for tier '{tier}' ({total_tokens}/{max_allowed} tokens). Payment required.",
            )
    finally:
        await redis_client.aclose()