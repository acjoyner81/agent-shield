"""Authentication and tenant authorization boundary."""

from functools import lru_cache
from os import environ

import jwt
from fastapi import Depends, Header, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from typing import Annotated

from config.settings import settings
from gateway.telemetry import emit_authz_failure

bearer_scheme = HTTPBearer()

@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"https://{settings.auth0_domain}/.well-known/jwks.json")

def verify_jwt(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict[str, object]:
    """Validate an Auth0 access token and return its claims."""
    token = credentials.credentials
    dev_mode = environ.get("DEV_MODE")
    print(f"DEBUG: verify_jwt called. token={token!r}, dev_mode={dev_mode!r}")
    
    if dev_mode == "true":
        if token == "dev-mock-token":
            print("DEBUG: Returning mock token")
            return {
                "sub": "user_dev_123",
                "https://agentshield.com/tenant_id": "tenant_alpha",
                "permissions": ["tools:execute", "logs:read"]
            }
        if token == "dev-unprivileged-token":
            print("DEBUG: Returning unprivileged token")
            return {
                "sub": "user_dev_456",
                "https://agentshield.com/tenant_id": "tenant_alpha",
                "permissions": ["logs:read"]
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

async def get_verified_tenant(
    request: Request,
    claims: dict[str, object] = Depends(verify_jwt),
) -> str:
    """Extracts verified tenant ID and binds it to the request state."""
    tenant_id = str(claims.get("https://agentshield.com/tenant_id"))
    user_id = str(claims.get("sub"))
    
    request.state.tenant_id = tenant_id
    request.state.user_id = user_id
    
    permissions = claims.get("permissions", [])
    request.state.permissions = set(permissions) if isinstance(permissions, list) else set()
    
    return tenant_id

def require_permission(required_scope: str):
    """
    Dependency factory that returns a function to verify a specific permission.
    """
    async def permission_checker(
        request: Request,
        tenant_id: Annotated[str, Depends(get_verified_tenant)],
    ):
        permissions = getattr(request.state, "permissions", set())
        if required_scope not in permissions:
            trace_id = "unknown"
            traceparent = request.headers.get("traceparent")
            if traceparent and "-" in traceparent:
                trace_id = traceparent.split("-")[1]
                
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

async def require_api_key(x_tenant_api_key: str | None = Header(default=None)) -> str:
    """Require an API key; production validation will use a hashed key store."""
    if not x_tenant_api_key:
        raise HTTPException(status_code=401, detail="Missing tenant API key")
    return x_tenant_api_key