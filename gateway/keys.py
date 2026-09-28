"""API key lifecycle management (Spec 0010).

Secrets are stored as SHA-256 digests only. Tenants own a hash of key records
(`tenant:keys:{tenant_id}`); verification looks up the digest index
(`apikey:{sha256(secret)}`) and enforces the Active / Rotated / Revoked
lifecycle with a configurable grace period for rotated keys.
"""

import hashlib
import json
import secrets
import logging
from datetime import datetime, timezone
from typing import List, Optional
import redis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from config.settings import settings
from gateway.auth import resolve_active_tenant
from gateway.telemetry import emit_key_rotation

logger = logging.getLogger("agentshield.keys")
router = APIRouter(prefix="/v1/keys", tags=["keys"])

r = redis.Redis.from_url(settings.redis_url, decode_responses=True)


class APIKeyCreateRequest(BaseModel):
    name: str
    permissions: Optional[List[str]] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "Create a key that may execute tools",
                    "description": "Grant only the scopes the integration actually needs.",
                    "value": {"name": "ci-pipeline", "permissions": ["tools:execute"]},
                },
                {
                    "summary": "Create a key with no scopes",
                    "description": "Read only. Suitable for usage reporting integrations.",
                    "value": {"name": "usage-reporter", "permissions": []},
                },
            ]
        }
    )


class RotateKeyRequest(BaseModel):
    name: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "Rotate and rename",
                    "description": "Omit `name` to keep the current key name.",
                    "value": {"name": "ci-pipeline-2"},
                }
            ]
        }
    )


class APIKeyResponse(BaseModel):
    key_id: str
    name: str
    key_prefix: str
    created_at: str
    status: str = "Active"
    version: int = 1
    rotated_at: Optional[str] = None
    superseded_by: Optional[str] = None
    permissions: List[str] = []
    secret_key: Optional[str] = None  # Only returned upon creation
    previous_key_id: Optional[str] = None  # Only returned upon rotation
    previous_key_rotated_at: Optional[str] = None  # Only returned upon rotation
    previous_key_superseded_by: Optional[str] = None  # Only returned upon rotation

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "Key as returned by a list call",
                    "description": (
                        "Only the masked prefix is ever stored and listed. "
                        "`secret_key` is null outside the creating call."
                    ),
                    "value": {
                        "key_id": "key_9f3ac21b",
                        "name": "ci-pipeline",
                        "key_prefix": "<masked>",
                        "created_at": "2026-09-24T10:15:00+00:00",
                        "status": "Active",
                        "version": 1,
                        "rotated_at": None,
                        "superseded_by": None,
                        "permissions": ["tools:execute"],
                        "secret_key": None,
                    },
                }
            ]
        }
    )


def get_redis_client() -> redis.Redis:
    return r


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask_prefix(raw_secret: str) -> str:
    return f"{raw_secret[:8]}••••••••••••{raw_secret[-4:]}"


def _new_secret() -> str:
    return f"sk_live_{secrets.token_urlsafe(24)}"


def _key_digest(raw_secret: str) -> str:
    return hashlib.sha256(raw_secret.encode()).hexdigest()


def _hash_key(tenant_id: str) -> str:
    return f"tenant:keys:{tenant_id}"


def _index_key(digest: str) -> str:
    return f"apikey:{digest}"


def _read_meta(r_client: redis.Redis, tenant_id: str, key_id: str) -> Optional[dict]:
    raw = r_client.hget(_hash_key(tenant_id), key_id)
    if not raw:
        return None
    try:
        meta = json.loads(raw) if isinstance(raw, str) else raw
        return meta if isinstance(meta, dict) else None
    except Exception:
        return None


@router.get("", response_model=List[APIKeyResponse])
async def list_api_keys(
    tenant_id: str = Depends(resolve_active_tenant),
    r_client: redis.Redis = Depends(get_redis_client),
) -> List[APIKeyResponse]:
    """List all API keys for the authenticated tenant with lifecycle fields."""
    keys_data = r_client.hgetall(_hash_key(tenant_id))

    result = []
    for kid, raw_meta in keys_data.items():
        meta = _read_meta_from_raw(raw_meta)
        if meta is None:
            continue
        result.append(APIKeyResponse(
            key_id=kid,
            name=meta.get("name", "API Key"),
            key_prefix=meta.get("prefix", "sk_live_••••••••••••"),
            created_at=meta.get("created_at", ""),
            status=meta.get("status", "Active"),
            version=int(meta.get("version", 1)),
            rotated_at=meta.get("rotated_at"),
            superseded_by=meta.get("superseded_by"),
            permissions=meta.get("permissions", []) or [],
        ))
    return result


@router.post("", response_model=APIKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: APIKeyCreateRequest,
    tenant_id: str = Depends(resolve_active_tenant),
    r_client: redis.Redis = Depends(get_redis_client),
    _perm: str = Depends(require_permission("keys:write")),
) -> APIKeyResponse:
    """Generate a new API key for the tenant, storing only its digest."""
    key_id = f"key_{secrets.token_hex(4)}"
    raw_secret = _new_secret()
    created_at = _iso_now()

    meta = {
        "name": body.name,
        "prefix": _mask_prefix(raw_secret),
        "created_at": created_at,
        "status": "Active",
        "version": 1,
        "secret_hash": _key_digest(raw_secret),
        "permissions": body.permissions or [],
    }

    pipe = r_client.pipeline()
    pipe.hset(_hash_key(tenant_id), key_id, json.dumps(meta))
    pipe.set(_index_key(meta["secret_hash"]), json.dumps({"tenant_id": tenant_id, "key_id": key_id}))
    pipe.execute()

    emit_key_rotation(
        tenant_id=tenant_id,
        key_id=key_id,
        action="create",
        status=meta["status"],
        version=meta["version"],
    )

    return APIKeyResponse(
        key_id=key_id,
        name=body.name,
        key_prefix=meta["prefix"],
        created_at=created_at,
        status="Active",
        version=1,
        permissions=body.permissions or [],
        secret_key=raw_secret,
    )


@router.post(
    "/{key_id}/rotate",
    response_model=APIKeyResponse,
    responses={
        401: {"description": "Not authenticated, or the key is revoked or its grace period expired."},
        404: {"description": "No such API key for the authenticated tenant."},
        409: {"description": "The key is already rotated or revoked and cannot be rotated again."},
    },
)
async def rotate_api_key(
    key_id: str,
    body: Optional[RotateKeyRequest] = None,
    tenant_id: str = Depends(resolve_active_tenant),
    r_client: redis.Redis = Depends(get_redis_client),
    _perm: str = Depends(require_permission("keys:write")),
) -> APIKeyResponse:
    """Rotate a key: mark it Rotated with a grace window and mint a successor.

    The read-check-write runs under WATCH on the tenant key hash so two
    concurrent rotates cannot both mint a successor (TOCTOU guard).
    """
    hash_key = _hash_key(tenant_id)
    with r_client.pipeline() as pipe:
        while True:
            try:
                pipe.watch(hash_key)
                meta = _read_meta(r_client, tenant_id, key_id)
                if meta is None:
                    raise HTTPException(status_code=404, detail="API key not found")
                if meta.get("status") == "Rotated":
                    raise HTTPException(status_code=409, detail="API key is already rotated")
                if meta.get("status") == "Revoked":
                    raise HTTPException(status_code=409, detail="Cannot rotate a revoked API key")

                version = int(meta.get("version", 1))
                new_key_id = f"key_{secrets.token_hex(4)}"
                new_secret = _new_secret()
                new_created = _iso_now()

                new_meta = {
                    "name": body.name if body and body.name else meta.get("name"),
                    "prefix": _mask_prefix(new_secret),
                    "created_at": new_created,
                    "status": "Active",
                    "version": version + 1,
                    "secret_hash": _key_digest(new_secret),
                    "permissions": meta.get("permissions", []) or [],
                }

                rotated_at = _iso_now()
                old_meta = dict(meta)
                old_meta["status"] = "Rotated"
                old_meta["rotated_at"] = rotated_at
                old_meta["superseded_by"] = new_key_id

                pipe.multi()
                pipe.hset(hash_key, key_id, json.dumps(old_meta))
                pipe.hset(hash_key, new_key_id, json.dumps(new_meta))
                pipe.set(_index_key(new_meta["secret_hash"]), json.dumps({"tenant_id": tenant_id, "key_id": new_key_id}))
                pipe.execute()
                break
            except redis.WatchError:
                continue

    emit_key_rotation(
        tenant_id=tenant_id,
        key_id=key_id,
        action="rotate",
        status="Rotated",
        version=version,
        rotated_at=rotated_at,
        superseded_by=new_key_id,
    )

    return APIKeyResponse(
        key_id=new_key_id,
        name=new_meta["name"],
        key_prefix=new_meta["prefix"],
        created_at=new_created,
        status="Active",
        version=version + 1,
        permissions=new_meta["permissions"],
        secret_key=new_secret,
        previous_key_id=key_id,
        previous_key_rotated_at=rotated_at,
        previous_key_superseded_by=new_key_id,
    )


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"description": "Not authenticated."},
        404: {"description": "No such API key for the authenticated tenant."},
    },
)
async def revoke_api_key(
    key_id: str,
    tenant_id: str = Depends(resolve_active_tenant),
    r_client: redis.Redis = Depends(get_redis_client),
    _perm: str = Depends(require_permission("keys:write")),
):
    """Revoke a key immediately, persisting it as a Revoked tombstone."""
    meta = _read_meta(r_client, tenant_id, key_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="API key not found")
    if meta.get("status") == "Revoked":
        return None

    meta["status"] = "Revoked"
    digest = meta.get("secret_hash")

    pipe = r_client.pipeline()
    pipe.hset(_hash_key(tenant_id), key_id, json.dumps(meta))
    if digest:
        pipe.delete(_index_key(digest))
    pipe.execute()

    emit_key_rotation(
        tenant_id=tenant_id,
        key_id=key_id,
        action="revoke",
        status="Revoked",
        version=int(meta.get("version", 1)),
        rotated_at=_iso_now(),
    )
    return None


def _read_meta_from_raw(raw_meta) -> Optional[dict]:
    try:
        meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
        return meta if isinstance(meta, dict) else None
    except Exception:
        return None