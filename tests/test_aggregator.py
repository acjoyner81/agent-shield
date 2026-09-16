import asyncio
import json
import pytest
import httpx
import redis.asyncio as redis
from gateway.aggregator import ship_to_splunk, dump_to_stdout, scrub_pii, scrub_pii_dict

# Mock environment variables
import os
os.environ["SPLUNK_HEC_URL"] = "http://mock-splunk:8088/services/collector/raw"
os.environ["SPLUNK_HEC_TOKEN"] = "mock-token"

@pytest.mark.asyncio
async def test_ship_to_splunk_success(mocker):
    # AC-1: Ship to Splunk
    batch = ['{"log": "test-1"}', '{"log": "test-2"}']
    mock_post = mocker.patch("httpx.AsyncClient.post", new_callable=mocker.AsyncMock)
    mock_post.return_value = mocker.Mock(status_code=200)
    
    async with httpx.AsyncClient() as client:
        await ship_to_splunk(client, batch)
    
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert "\n".join(batch) == kwargs["content"]
    assert kwargs["headers"]["Authorization"] == "Splunk mock-token"

@pytest.mark.asyncio
async def test_stdout_fallback(mocker):
    # AC-5: NDJSON fallback
    batch = ['{"log": "critical-failure"}']
    mock_stdout = mocker.patch("sys.stdout.write")
    
    await dump_to_stdout(batch)
    
    # Verify output is the log string followed by newline
    # Note: print() calls write() multiple times
    found = any('{"log": "critical-failure"}' in call.args[0] for call in mock_stdout.call_args_list)
    assert found

@pytest.mark.asyncio
async def test_pii_scrubbing():
    # Standard verification of PII masks
    test_data = "My email is test@example.com and my key is mock_secret_key_12345"
    scrubbed = await scrub_pii(test_data)
    assert "test@example.com" not in scrubbed
    assert "mock_secret" not in scrubbed
    assert "[MASKED]" in scrubbed

@pytest.mark.asyncio
async def test_pii_dict_scrubbing():
    data = {"user": "admin", "email": "test@example.com", "meta": {"token": "eyJ123.abc.def"}}
    scrubbed = await scrub_pii_dict(data)
    assert scrubbed["email"] == "[MASKED]"
    assert scrubbed["meta"]["token"] == "[MASKED]"
    assert scrubbed["user"] == "admin"
