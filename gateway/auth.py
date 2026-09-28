"""Authentication and tenant authorization boundary."""

import hashlib
import json
from datetime import datetime, timezone
from functools import lru_cache
from os import environ
from typing import Annotated, Optional

import jwt
import redis
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import settings
from gateway.telemetry import emit_authz_failure, emit_key_rotation

bearer_scheme = HTTPBearer(auto_error=False)

_r = redis.Redis.from_url(settings.redis_url, decode_responses=True)


def get_redis_client() -> redis.Redis:
    return _r


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"https://{settings.auth0_domain}/.well-known/jwks.json")


def _authorization_error(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_token_credentials(token: str) -> dict[str, object]:
    """Validate an Auth0 access token string and return its claims."""
    dev_mode = environ.get("DEV_MODE")
    if dev_mode == "true":
        if token == "dev-mock-token":
            return {
                "sub": "user_dev_123",
                "https://agentshield.com/tenant_id": "tenant_alpha",
                "permissions": ["tools:execute", "logs:read"],
            }
        if token == "dev-unprivileged-token":
            return {
                "sub": "user_dev_456",
                "https://agentshield.com/tenant_id": "tenant_alpha",
                "permissions": ["logs:read"],
            }

    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.auth0_audience,
            issuer=settings.auth0_issuer,
        )

        tenant_id = claims.get("https://agentshield.com/tenant_id")
        if not tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token missing mandatory tenant identification claim",
            )

        return claims
    except jwt.PyJWTError as exc:
        raise _authorization_error("Invalid access token") from exc


def verify_jwt(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> dict[str, object]:
    """Validate an Auth0 access token and return its claims."""
    if credentials is None:
        raise _authorization_error("Not authenticated")
    return verify_token_credentials(credentials.credentials)


def _bind_tenant_state(request: Request, claims: dict[str, object]) -> str:
    """Bind a verified principal's tenant, user, and permissions to request state."""
    tenant_id = str(claims.get("https://agentshield.com/tenant_id"))
    user_id = str(claims.get("sub"))

    request.state.tenant_id = tenant_id
    request.state.user_id = user_id

    permissions = claims.get("permissions", [])
    request.state.permissions = set(permissions) if isinstance(permissions, list) else set()

    return tenant_id


async def get_verified_tenant(
    request: Request,
    claims: dict[str, object] = Depends(verify_jwt),
) -> str:
    """Extracts verified tenant ID and binds it to the request state."""
    return _bind_tenant_state(request, claims)


def _trace_ids(request: Request) -> tuple[str, str]:
    traceparent = request.headers.get("traceparent")
    if traceparent and "-" in traceparent:
        trace_id = traceparent.split("-")[1]
    else:
        trace_id = "unknown"
    return trace_id, trace_id[:16] if trace_id and len(trace_id) >= 16 else "unknown"


async def verify_api_key(
    request: Request,
    x_tenant_api_key: Annotated[Optional[str], Header()] = None,
    r_client: Optional[redis.Redis] = None,
) -> str:
    """Verify an API key against the hashed store, enforce lifecycle and grace."""
    api_key = x_tenant_api_key or request.headers.get("X-Tenant-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-Tenant-API-Key header")

    digest = hashlib.sha256(api_key.encode()).hexdigest()
    if r_client is None:
        r_client = get_redis_client()

    index_raw = r_client.get(f"apikey:{digest}")
    if not index_raw:
        raise HTTPException(status_code=401, detail="Unknown API key")
    try:
        index = json.loads(index_raw)
    except Exception:
        raise HTTPException(status_code=401, detail="Unknown API key")

    tenant_id = index.get("tenant_id")
    key_id = index.get("key_id")
    if not tenant_id or not key_id:
        raise HTTPException(status_code=401, detail="Unknown API key")

    try:
        meta_raw = r_client.hget(f"tenant:keys:{tenant_id}", key_id)
        meta = json.loads(meta_raw) if meta_raw and isinstance(meta_raw, str) else meta_raw
        if not isinstance(meta, dict):
            raise ValueError("malformed key meta")
    except Exception:
        raise HTTPException(status_code=401, detail="Unknown API key")

    trace_id, span_id = _trace_ids(request)

    if meta.get("status") == "Active":
        pass
    elif meta.get("status") == "Rotated":
        rotated_at = meta.get("rotated_at")
        try:
            rotated_at_dt = datetime.fromisoformat(rotated_at)
        except Exception:
            raise HTTPException(status_code=401, detail="Unknown API key")
        grace = settings.api_key_grace_period_seconds
        if (datetime.now(timezone.utc) - rotated_at_dt).total_seconds() > grace:
            raise HTTPException(
                status_code=401,
                detail="API key rotation grace period expired",
            )
        # Emit at most one grace-usage audit event per key per hour so a hot
        # rotated key does not spam the telemetry stream or slow the auth path.
        emitted = r_client.set(f"apikey:grace:event:{key_id}", "1", ex=3600, nx=True)
        if emitted:
            emit_key_rotation(
                tenant_id=tenant_id,
                key_id=key_id,
                action="grace",
                status="Rotated",
                version=int(meta.get("version", 1)),
                rotated_at=rotated_at,
                superseded_by=meta.get("superseded_by"),
                trace_id=trace_id,
                span_id=span_id,
            )
    elif meta.get("status") == "Revoked":
        raise HTTPException(status_code=401, detail="API key revoked")
    else:
        raise HTTPException(status_code=401, detail="Unknown API key")

    request.state.tenant_id = tenant_id
    request.state.user_id = key_id
    permissions = meta.get("permissions") or []
    request.state.permissions = set(permissions) if isinstance(permissions, list) else set()

    return tenant_id


async def resolve_active_tenant(request: Request) -> str:
    """Resolve the authenticated principal: Bearer JWT first, API key fallback."""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):].strip()
        claims = verify_token_credentials(token)
        return _bind_tenant_state(request, claims)

    api_key = request.headers.get("X-Tenant-API-Key")
    if api_key:
        return await verify_api_key(request, api_key)

    raise _authorization_error("Not authenticated")


def require_permission(required_scope: str):
    """
    Dependency factory that returns a function to verify a specific permission.
    """
    async def permission_checker(
        request: Request,
        tenant_id: Annotated[str, Depends(resolve_active_tenant)],
    ):
        permissions = getattr(request.state, "permissions", set())
        if required_scope not in permissions:
            traceparent = request.headers.get("traceparent")
            if traceparent and "-" in traceparent:
                trace_id = traceparent.split("-")[1]
            else:
                trace_id = "unknown"

            user_id = getattr(request.state, "user_id", "unknown")
            span_id = trace_id[:16] if trace_id and len(trace_id) >= 16 else "unknown"

            # Synchronous direct call (removed asyncio.create_task and removed duplicate call)
            emit_authz_failure(
                tenant_id=tenant_id,
                user_id=user_id,
                trace_id=trace_id,
                span_id=span_id,
                missing_scope=required_scope,
            )

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: missing required scope '{required_scope}'",
            )
        return True
    return permission_checker