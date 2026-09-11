"""Authentication and tenant authorization boundary."""

from functools import lru_cache

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import settings


bearer_scheme = HTTPBearer()


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"https://{settings.auth0_domain}/.well-known/jwks.json")


def verify_jwt(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict[str, object]:
    """Validate an Auth0 access token and return its claims."""
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(credentials.credentials)
        return jwt.decode(
            credentials.credentials,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.auth0_audience,
            issuer=settings.auth0_issuer,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def require_api_key(x_tenant_api_key: str | None = Header(default=None)) -> str:
    """Require an API key; production validation will use a hashed key store."""
    if not x_tenant_api_key:
        raise HTTPException(status_code=401, detail="Missing tenant API key")
    return x_tenant_api_key
