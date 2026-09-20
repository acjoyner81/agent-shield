from unittest.mock import MagicMock, patch
import pytest
from fastapi import HTTPException

from gateway.llm_proxy import (
    calculate_prompt_hash,
    check_tenant_budget,
    completion_proxy,
)


def test_prompt_hash_consistency():
    hash1 = calculate_prompt_hash("gpt-4o", [{"role": "user", "content": "hello"}])
    hash2 = calculate_prompt_hash("gpt-4o", [{"role": "user", "content": "hello"}])
    assert hash1 == hash2


def test_budget_exhaustion_rejects_request():
    mock_redis = MagicMock()

    # Distinguish exact monthly spend key from max budget configuration key
    def redis_get_side_effect(key: str):
        if key.startswith("budget:"):
            return "100.0"
        if key.endswith(":max_budget"):
            return "50.0"
        return None

    mock_redis.get.side_effect = redis_get_side_effect

    within_budget, spend, max_b = check_tenant_budget(mock_redis, "tenant_beta")
    assert within_budget is False
    assert spend == 100.0
    assert max_b == 50.0


@pytest.mark.asyncio
async def test_completion_proxy_cache_hit():
    mock_redis = MagicMock()
    mock_redis.get.side_effect = lambda k: "50.0" if "max_budget" in k else '{"choices": [], "cached": false}'

    with patch("gateway.llm_proxy.get_redis_client", return_value=mock_redis), \
         patch("gateway.llm_proxy.check_tenant_budget", return_value=(True, 0.0, 50.0)), \
         patch("gateway.llm_proxy.litellm.completion") as mock_litellm:

        res = await completion_proxy("tenant_test", "gpt-4o", [{"role": "user", "content": "hi"}])
        assert res["cached"] is True
        mock_litellm.assert_not_called()


@pytest.mark.asyncio
async def test_completion_proxy_fallback_chain():
    mock_redis = MagicMock()
    
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {"choices": [{"message": {"content": "ok"}}]}

    with patch("gateway.llm_proxy.get_redis_client", return_value=mock_redis), \
         patch("gateway.llm_proxy.check_tenant_budget", return_value=(True, 0.0, 50.0)), \
         patch("gateway.llm_proxy.get_cached_llm_response", return_value=None), \
         patch("gateway.llm_proxy.litellm.completion", side_effect=[Exception("Primary model down"), mock_response]) as mock_litellm:

        res = await completion_proxy(
            tenant_id="tenant_test",
            model="gpt-4o-failing",
            messages=[{"role": "user", "content": "ping"}],
            fallbacks=["gpt-4o-mini"],
        )

        assert res["cached"] is False
        assert mock_litellm.call_count == 2
        