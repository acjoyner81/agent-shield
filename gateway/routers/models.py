"""Model management endpoints for listing and setting tenant defaults."""

import yaml
import os
from typing import Annotated, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config.settings import settings
from gateway.auth import resolve_active_tenant, require_permission
import redis

router = APIRouter(prefix="/v1/models", tags=["models"])
r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
REGISTRY_PATH = "gateway/models_registry.yaml"

class ModelDefinition(BaseModel):
    model_id: str
    display_name: str
    provider_id: str
    input_cost_per_1k: float
    output_cost_per_1k: float

class DefaultModelRequest(BaseModel):
    model: str

def load_registry() -> list[ModelDefinition]:
    """Load model definitions from the static YAML file."""
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return [ModelDefinition(**m) for m in data.get("models", [])]
    except Exception as e:
        # Fallback to empty list if file is missing or corrupt
        return []

@router.get("", response_model=list[ModelDefinition])
async def list_models():
    """Return all available models and their rate cards."""
    return load_registry()

@router.get("/default")
async def get_default_model(
    tenant_id: Annotated[str, Depends(resolve_active_tenant)],
) -> dict[str, str]:
    """Return the current default model for the authenticated tenant."""
    config_key = f"tenant:{tenant_id}:config"
    default = r.hget(config_key, "default_model")
    
    if not default:
        # Fallback to the first model in the registry
        registry = load_registry()
        if registry:
            default = registry[0].model_id
        else:
            raise HTTPException(status_code=503, detail="No models available in registry")
            
    return {"default_model": default}

@router.post("/default")
async def set_default_model(
    payload: DefaultModelRequest,
    tenant_id: Annotated[str, Depends(resolve_active_tenant)],
    _admin: Annotated[bool, Depends(require_permission("billing:admin"))] = True,
) -> dict[str, str]:
    """Set the default LLM model for the authenticated tenant."""
    # Verify the model exists in the registry
    registry = load_registry()
    if not any(m.model_id == payload.model for m in registry):
        raise HTTPException(status_code=400, detail=f"Model {payload.model} not found in registry")

    # Store in the tenant config hash
    config_key = f"tenant:{tenant_id}:config"
    r.hset(config_key, "default_model", payload.model)
    
    return {"status": "success", "default_model": payload.model}
