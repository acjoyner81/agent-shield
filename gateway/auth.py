"""Authentication and tenant authorization boundary."""

import hashlib
import json
import logging
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

# Claim names on the AgentShield access token. Both are namespaced and the
# gateway reads these exact strings, so the Post-Login Action and this module
# have to change together. See auth0/actions/add-tenant-claims.js for why the
# namespace on `permissions` is load-bearing rather than stylistic.
TENANT_CLAIM = "https://api.agentshield.local/tenant_id"
PERMISSIONS_CLAIM = "https://api.agentshield.local/permissions"

bearer_scheme = HTTPBearer(auto_error=False)

logger = logging.getLogger(__name__)

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
        # A dev stand-in grants tools:execute and keys:write with no signature
        # check, so it is only ever safe while this process is a local
        # development build. Refuse it anywhere else rather than trusting the
        # deployment to remember the flag, which is how it was left hardcoded
        # "true" in compose and served credentials to anything that could reach
        # the port.
        if settings.app_env != "development":
            raise RuntimeError(
                "DEV_MODE is enabled while APP_ENV is "
                f"{settings.app_env!r}. The dev stand-in tokens skip signature "
                "verification and grant keys:write, so they are refused outside "
                "development. Set DEV_MODE=false, or APP_ENV=development."
            )
        # These stand-ins must mirror the claim names Auth0 actually issues, or
        # they would test a token shape that cannot exist and hide a real
        # mismatch. In particular `permissions` is namespaced, because the bare
        # name is reserved by Auth0's RBAC and never arrives on a real token.
        if token == "dev-mock-token":
            logger.warning(
                "served unauthenticated dev stand-in claims for dev-mock-token"
            )
            return {
                "sub": "user_dev_123",
                TENANT_CLAIM: "tenant_alpha",
                PERMISSIONS_CLAIM: ["tools:execute", "logs:read", "keys:write"],
            }
        if token == "dev-unprivileged-token":
            logger.warning(
                "served unauthenticated dev stand-in claims for "
                "dev-unprivileged-token"
            )
            return {
                "sub": "user_dev_456",
                TENANT_CLAIM: "tenant_alpha",
                PERMISSIONS_CLAIM: ["logs:read"],
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

        tenant_id = claims.get("https://api.agentshield.local/tenant_id")
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
    tenant_id = str(claims.get(TENANT_CLAIM))
    user_id = str(claims.get("sub"))

    request.state.tenant_id = tenant_id
    request.state.user_id = user_id
    request.state.principal_verified = True

    # The claim is namespaced and must be read under that exact name. Auth0
    # reserves the bare `permissions` name for its own RBAC, and its documented
    # behaviour on a collision is that the transaction succeeds while the custom
    # claim is quietly not added. The Action used to set the bare name, so every
    # real user token arrived with no permissions claim at all and every gated
    # route 403'd. An unnamespaced read here would look like it was working.
    #
    # Absence is recorded separately from emptiness. A claim that is missing
    # means the Action never stamped this token, which is an Auth0 configuration
    # fault, while an empty claim means the user genuinely holds nothing. Those
    # need very different fixes, and collapsing both to an empty set is what let
    # this hide: the 403 said "missing required scope", which points at the user
    # and away from the login configuration that actually broke.
    raw_permissions = claims.get(PERMISSIONS_CLAIM)
    if isinstance(raw_permissions, list):
        request.state.permissions = {
            str(permission) for permission in raw_permissions if isinstance(permission, str)
        }
        request.state.permissions_claim_present = True
    else:
        request.state.permissions = set()
        request.state.permissions_claim_present = False

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
    *,
    r_client: Optional[redis.Redis] = None,
) -> str:
    """Verify an API key against the hashed store, enforce lifecycle and grace.

    Called directly rather than as a dependency, so the store is a keyword-only
    injection point and the header stays the single place the key is read.
    """
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
    request.state.principal_verified = True
    permissions = meta.get("permissions") or []
    request.state.permissions = set(permissions) if isinstance(permissions, list) else set()

    return tenant_id


async def resolve_active_tenant(request: Request) -> str:
    """Resolve the authenticated principal: Bearer JWT first, API key fallback.

    The app-level rate limiter resolves the principal before the route does, so a
    credential is verified once per request and the tenant is replayed from the
    state the first verification bound.
    """
    if getattr(request.state, "principal_verified", False) and getattr(request.state, "tenant_id", None):
        return str(request.state.tenant_id)

    auth_header = request.headers.get("Authorization")
    if auth_header is not None:
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            # A present-but-unusable Authorization header is a hard failure, never
            # a reason to fall through to the weaker API key credential.
            raise _authorization_error("Unsupported or malformed Authorization header")
        return _bind_tenant_state(request, verify_token_credentials(token.strip()))

    api_key = request.headers.get("X-Tenant-API-Key")
    if api_key:
        return await verify_api_key(request, x_tenant_api_key=api_key)

    raise _authorization_error("Not authenticated")


def _assert_permission(request: Request, required_scope: str, tenant_id: str) -> None:
    """The one implementation of the permission check. Raises 403 if not granted.

    Both permission dependencies call this rather than each rolling its own
    comparison. They used to be independent: one in this module and one in
    gateway/dependencies.py, with different 403 messages and only one of them
    emitting authz telemetry. Two gates that can drift is how a check ends up
    enforced on one route and skipped on another, and the copy guarding the key
    write routes was the one without telemetry.
    """
    permissions = getattr(request.state, "permissions", set())
    if required_scope in permissions:
        return

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

    # Name the actual cause. "Missing required scope" blames the caller, but when
    # the claim is absent entirely the caller may hold every permission in the
    # system and the real fault is that Auth0 never issued the claim. That is
    # exactly how a namespacing bug in the Post-Login Action presented as a
    # permissions problem for as long as it went unnoticed.
    if not getattr(request.state, "permissions_claim_present", True):
        detail = (
            f"Permission denied: this token carries no '{PERMISSIONS_CLAIM}' claim, "
            "so no permissions can be evaluated. The Auth0 Post-Login Action did "
            "not stamp this token. Redeploy auth0/actions/add-tenant-claims.js and "
            "sign in again."
        )
    else:
        detail = f"Permission denied: missing required scope '{required_scope}'"

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def require_permission(required_scope: str):
    """
    Dependency factory that returns a function to verify a specific permission.
    """
    async def permission_checker(
        request: Request,
        tenant_id: Annotated[str, Depends(resolve_active_tenant)],
    ):
        _assert_permission(request, required_scope, tenant_id)
        return True
    return permission_checker