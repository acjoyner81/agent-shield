import pytest
import asyncio
import os
from unittest.mock import AsyncMock, patch
import httpx
from gateway.main import app

class TestQuotaConcurrency:
    @pytest.mark.asyncio
    @patch("gateway.dependencies.aioredis.from_url")
    async def test_concurrent_requests_near_quota_limit(self, mock_from_url):
        # Ensure development auth mode is enabled so tokens pass validation
        os.environ["DEV_MODE"] = "true"
        
        mock_redis_client = AsyncMock()
        request_counter = 0
        
        async def mock_get(key):
            nonlocal request_counter
            request_counter += 1
            # Allow the first 2 concurrent requests through ("false"), then throttle the rest ("true")
            if request_counter <= 2:
                return "false"
            return "true"

        mock_redis_client.get = mock_get
        mock_from_url.return_value = mock_redis_client

        # Fire 5 concurrent requests using an async HTTP client over the FastAPI transport
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            tasks = [
                ac.post(
                    "/v1/chat/completions",
                    headers={
                        "Authorization": "Bearer dev-mock-token",
                        "X-Tenant-ID": "tenant_concurrency_test",
                        "X-Tenant-Tier": "free"
                    },
                    json={"prompt": f"Concurrent query {i}", "model": "gpt-4o"}
                )
                for i in range(5)
            ]
            responses = await asyncio.gather(*tasks)

        status_codes = [r.status_code for r in responses]
        
        # Verify that early requests succeeded (200 OK) and subsequent ones were throttled (402)
        assert 200 in status_codes
        assert 402 in status_codes
        
        # Ensure no unexpected server errors (5xx) occurred under concurrent pressure
        for code in status_codes:
            assert code < 500