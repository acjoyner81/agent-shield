import hashlib
import json
import os
import time
from typing import Dict, Any, Optional, Tuple
from fastapi import HTTPException, status
import litellm
import redis

from config.settings import settings
from gateway.rate_limit import get_redis_client

# Suppress noisy LiteLLM logs in production
litellm.suppress_debug_info = True

DEFAULT_TENANT_MONTHLY_BUDGET = 50.0  # $50 default monthly cap

# Ensure LangSmith environment variables are propagated into os.environ for LiteLLM
os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGCHAIN_TRACING_V2", "true")
os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGCHAIN_PROJECT", "agent-shield-local")
if os.getenv("LANGCHAIN_API_KEY"):
    os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGCHAIN_API_KEY")

# Enable LangSmith callbacks inside LiteLLM
if os.environ.get("LANGCHAIN_TRACING_V2") == "true":
    litellm.success_callback = ["langsmith"]
    litellm.failure_callback = ["langsmith"]


def calculate_prompt_hash(model: str, messages: list) -> str:
    """Generates a deterministic SHA256 hash for exact/semantic prompt caching."""
    payload = json.dumps({"model": model, "messages": messages}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def check_tenant_budget(
    r_client: redis.Redis, tenant_id: str, estimated_cost: float = 0.001
) -> Tuple[bool, float, float]:
    """
    Checks if tenant has exceeded their monthly budget.
    Redis key: budget:{tenant_id}:{YYYY-MM}
    """
    current_month = time.strftime("%Y-%m")
    key = f"budget:{tenant_id}:{current_month}"

    try:
        current_usage = float(r_client.get(key) or 0.0)
    except Exception:
        current_usage = 0.0

    # Retrieve max budget limit set in Redis or default settings
    try:
        max_budget = float(
            r_client.get(f"tenant:{tenant_id}:max_budget") or DEFAULT_TENANT_MONTHLY_BUDGET
        )
    except Exception:
        max_budget = DEFAULT_TENANT_MONTHLY_BUDGET

    if current_usage + estimated_cost > max_budget:
        return False, current_usage, max_budget

    return True, current_usage, max_budget


def record_tenant_usage(r_client: redis.Redis, tenant_id: str, actual_cost: float) -> None:
    """Records real API costs incurred by the tenant for the current month."""
    current_month = time.strftime("%Y-%m")
    key = f"budget:{tenant_id}:{current_month}"
    try:
        pipe = r_client.pipeline()
        pipe.incrbyfloat(key, actual_cost)
        pipe.expire(key, 60 * 60 * 24 * 35)  # Expire after 35 days
        pipe.execute()
    except Exception:
        pass


def get_cached_llm_response(r_client: redis.Redis, cache_key: str) -> Optional[Dict[str, Any]]:
    """Retrieves cached response from Redis if available."""
    try:
        cached = r_client.get(f"cache:llm:{cache_key}")
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    return None


def cache_llm_response(
    r_client: redis.Redis, cache_key: str, response_data: Dict[str, Any], ttl: int = 3600
) -> None:
    """Caches LLM completion response in Redis."""
    try:
        r_client.setex(
            f"cache:llm:{cache_key}",
            ttl,
            json.dumps(response_data),
        )
    except Exception:
        pass


async def completion_proxy(
    tenant_id: str,
    model: str,
    messages: list,
    fallbacks: Optional[list] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Main LLM completion gateway proxy executing:
    1. Budget pre-checks
    2. Semantic/exact prompt cache lookup
    3. LiteLLM invocation with automatic fallbacks
    4. Post-execution token cost recording & caching
    """
    r_client = get_redis_client()

    # 1. Budget Check
    within_budget, current_spend, max_budget = check_tenant_budget(r_client, tenant_id)
    if not within_budget:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "Tenant budget limit exceeded",
                "current_spend_usd": current_spend,
                "max_budget_usd": max_budget,
            },
        )

    # 2. Semantic / Prompt Cache Check
    cache_key = calculate_prompt_hash(model, messages)
    cached_response = get_cached_llm_response(r_client, cache_key)
    if cached_response:
        cached_response["cached"] = True
        return cached_response

    # 3. Execution with Fallback Chain
    models_to_try = [model] + (fallbacks or [])
    last_exception = None
    response = None

    # Pass metadata to LiteLLM so LangSmith tags the project and tenant properly
    metadata = kwargs.pop("metadata", {})
    metadata.update({
        "project_name": os.getenv("LANGCHAIN_PROJECT", "agent-shield-local"),
        "tenant_id": tenant_id,
    })

    for target_model in models_to_try:
        try:
            response = litellm.completion(
                model=target_model,
                messages=messages,
                metadata=metadata,
                **kwargs,
            )
            break
        except Exception as e:
            last_exception = e
            continue

    if response is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"All model routes failed. Last error: {str(last_exception)}",
        )

    # Convert LiteLLM response object to dictionary
    response_dict = response.model_dump() if hasattr(response, "model_dump") else dict(response)
    response_dict["cached"] = False

    # 4. Calculate actual cost & update budget tracker
    try:
        cost = litellm.completion_cost(completion_response=response)
    except Exception:
        cost = 0.001  # Fallback minimal cost estimate

    record_tenant_usage(r_client, tenant_id, cost)

    # 5. Cache response
    cache_llm_response(r_client, cache_key, response_dict)

    return response_dict