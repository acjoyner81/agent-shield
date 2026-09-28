import asyncio
import json
import pytest
import httpx
import redis.asyncio as redis
import gateway.aggregator as aggregator_module
from gateway.aggregator import ship_to_splunk, dump_to_stdout, scrub_pii, scrub_pii_dict
from gateway.telemetry import emit_event

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


@pytest.mark.asyncio
async def test_aggregator_ships_stream_event_before_acknowledging(mocker):
    publisher = mocker.Mock()
    emit_event(
        tenant_id="tenant_test",
        event_type="agentshield.telemetry.request.completed",
        data={"method": "GET", "path": "/health", "status_code": 200, "latency_ms": 1.0},
        redis_client=publisher,
    )
    payload = publisher.xadd.call_args.args[1]["payload"]
    publisher.xadd.assert_called_once_with("telemetry:queue", {"payload": payload})

    redis_client = mocker.AsyncMock()
    redis_client.lpop.side_effect = RuntimeError("LPOP called")
    redis_client.xreadgroup.side_effect = [
        [("telemetry:queue", [("1-0", {"payload": payload})])],
        RuntimeError("stop"),
    ]

    mocker.patch("gateway.aggregator.redis.from_url", return_value=redis_client)
    mocker.patch.object(aggregator_module, "BATCH_SIZE", 1)

    client_context = mocker.MagicMock()
    http_client = mocker.AsyncMock()
    client_context.__aenter__.return_value = http_client
    client_context.__aexit__.return_value = None
    mocker.patch("gateway.aggregator.httpx.AsyncClient", return_value=client_context)

    order = []
    ship = mocker.patch(
        "gateway.aggregator.ship_to_splunk",
        new=mocker.AsyncMock(side_effect=lambda *_: order.append("ship")),
    )
    redis_client.xack.side_effect = lambda *_: order.append("ack")

    with pytest.raises(RuntimeError, match="stop"):
        await aggregator_module.run_aggregator()

    redis_client.xgroup_create.assert_awaited_once_with(
        name="telemetry:queue",
        groupname=aggregator_module.GROUP_NAME,
        id="0",
        mkstream=True,
    )
    assert redis_client.xreadgroup.await_args_list[0].kwargs["streams"] == {
        "telemetry:queue": ">"
    }
    ship.assert_awaited_once_with(http_client, [payload])
    redis_client.xack.assert_awaited_once_with(
        "telemetry:queue", aggregator_module.GROUP_NAME, "1-0"
    )
    redis_client.lpop.assert_not_awaited()
    assert order == ["ship", "ack"]


@pytest.mark.asyncio
async def test_aggregator_completes_fallback_before_acknowledging(mocker):
    payload = '{"event_id":"evt_0123456789abcdef0123456789abcdef","tenant_id":"tenant_test"}'
    redis_client = mocker.AsyncMock()
    redis_client.xreadgroup.side_effect = [
        [("telemetry:queue", [("2-0", {"payload": payload})])],
        RuntimeError("stop"),
    ]

    mocker.patch("gateway.aggregator.redis.from_url", return_value=redis_client)
    mocker.patch.object(aggregator_module, "BATCH_SIZE", 1)
    mocker.patch("gateway.aggregator.asyncio.sleep", new=mocker.AsyncMock())

    client_context = mocker.MagicMock()
    client_context.__aenter__.return_value = mocker.AsyncMock()
    client_context.__aexit__.return_value = None
    mocker.patch("gateway.aggregator.httpx.AsyncClient", return_value=client_context)

    pipeline = mocker.MagicMock()
    pipeline.__aenter__ = mocker.AsyncMock(return_value=pipeline)
    pipeline.__aexit__ = mocker.AsyncMock(return_value=None)
    redis_client.pipeline = mocker.MagicMock(return_value=pipeline)

    order = []

    def fail_ship(*_):
        order.append("hec")
        raise RuntimeError("Splunk unavailable")

    ship = mocker.patch(
        "gateway.aggregator.ship_to_splunk",
        new=mocker.AsyncMock(side_effect=fail_ship),
    )
    pipeline.execute = mocker.AsyncMock(side_effect=lambda: order.append("dlq"))
    dump = mocker.patch(
        "gateway.aggregator.dump_to_stdout",
        new=mocker.AsyncMock(side_effect=lambda *_: order.append("stdout")),
    )
    redis_client.xack.side_effect = lambda *_: order.append("ack")

    with pytest.raises(RuntimeError, match="stop"):
        await aggregator_module.run_aggregator()

    assert ship.await_count == aggregator_module.MAX_RETRIES
    pipeline.rpush.assert_called_once_with("telemetry:dlq", payload)
    dump.assert_awaited_once_with([payload])
    redis_client.xack.assert_awaited_once_with(
        "telemetry:queue", aggregator_module.GROUP_NAME, "2-0"
    )
    assert order[-3:] == ["dlq", "stdout", "ack"]
