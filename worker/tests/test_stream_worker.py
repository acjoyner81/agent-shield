import json
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from worker.stream_worker import TelemetryWorker, STREAM_KEY, GROUP_NAME, CONSUMER_NAME

@pytest.mark.asyncio
async def test_ensure_consumer_group_creates_group():
    worker = TelemetryWorker()
    worker.client = AsyncMock()
    
    await worker._ensure_consumer_group()
    
    worker.client.xgroup_create.assert_called_once_with(
        name=STREAM_KEY,
        groupname=GROUP_NAME,
        id="0",
        mkstream=True
    )

@pytest.mark.asyncio
async def test_ensure_consumer_group_handles_busygroup():
    worker = TelemetryWorker()
    worker.client = AsyncMock()
    
    import redis
    worker.client.xgroup_create.side_effect = redis.ResponseError("BUSYGROUP Consumer Group name already exists")
    
    # Should handle BUSYGROUP without raising an exception
    await worker._ensure_consumer_group()

@pytest.mark.asyncio
async def test_worker_processes_and_acks_messages():
    worker = TelemetryWorker()
    worker.client = AsyncMock()
    
    fake_payload = {"event_type": "request_completed", "tenant_id": "tenant_123"}
    fake_stream_data = [
        (
            STREAM_KEY,
            [
                ("1000-0", {"payload": json.dumps(fake_payload)}),
                ("1000-1", {"payload": json.dumps(fake_payload)})
            ]
        )
    ]
    
    # Return fake data on first read, then stop loop
    worker.client.xreadgroup.side_effect = [fake_stream_data, asyncio.CancelledError()]
    
    with patch.object(worker, "process_event", new_callable=AsyncMock) as mock_process:
        try:
            await worker.run()
        except asyncio.CancelledError:
            pass
            
        assert mock_process.call_count == 2
        worker.client.xack.assert_called_once_with(
            STREAM_KEY,
            GROUP_NAME,
            "1000-0",
            "1000-1"
        )


@pytest.mark.asyncio
async def test_process_event_routes_token_usage_to_metering():
    """A real agentshield.token.usage CloudEvent must reach process_token_event."""
    worker = TelemetryWorker()
    worker.sync_client = MagicMock()

    payload = {
        "specversion": "1.0",
        "event_id": "evt_abc123",
        "timestamp": "2026-09-23T10:00:00Z",
        "type": "agentshield.token.usage",
        "tenant_id": "tenant_alpha",
        "data": {"input": 100, "output": 50, "total": 150, "model": "gpt-4o"},
    }

    with patch(
        "worker.stream_worker.process_token_event", return_value=True
    ) as mock_process:
        await worker.process_event("1000-0", payload)
        mock_process.assert_called_once_with(payload, r_client=worker.sync_client)


@pytest.mark.asyncio
async def test_process_event_ignores_non_token_events():
    """Non token-usage events must be a no-op, not an error."""
    worker = TelemetryWorker()
    worker.sync_client = MagicMock()

    payload = {
        "type": "agentshield.telemetry.request.completed",
        "tenant_id": "tenant_alpha",
        "data": {"method": "POST", "path": "/v1/tools/execute"},
    }

    with patch(
        "worker.stream_worker.process_token_event", return_value=True
    ) as mock_process:
        await worker.process_event("1000-0", payload)
        mock_process.assert_not_called()


@pytest.mark.asyncio
async def test_process_event_routes_security_events_to_meta_counters():
    """Security and request events must reach process_meta_event, not token metering."""
    worker = TelemetryWorker()
    worker.sync_client = MagicMock()

    payloads = [
        {
            "event_id": "evt_authz_1",
            "timestamp": "2026-09-24T10:00:00Z",
            "type": "agentshield.security.authz_failure",
            "tenant_id": "tenant_alpha",
            "data": {"rbac_passed": False},
        },
        {
            "event_id": "evt_rate_1",
            "timestamp": "2026-09-24T10:00:01Z",
            "type": "agentshield.security.rate_limit_exceeded",
            "tenant_id": "tenant_alpha",
            "data": {"rate_limit_remaining": 0},
        },
    ]

    with patch("worker.stream_worker.process_meta_event", return_value=True) as mock_meta:
        for payload in payloads:
            await worker.process_event("1000-0", payload)

    assert mock_meta.call_count == 2
    assert all(call.args[0]["type"].startswith("agentshield.security.") for call in mock_meta.call_args_list)


@pytest.mark.asyncio
async def test_process_event_ignores_unrelated_event_types():
    """Key rotation and billing events carry no dashboard counter, so they are skipped."""
    worker = TelemetryWorker()
    worker.sync_client = MagicMock()

    payload = {
        "type": "agentshield.security.key_rotation",
        "tenant_id": "tenant_alpha",
        "data": {"key_id": "key_1", "action": "rotate", "status": "Active"},
    }

    with patch("worker.stream_worker.process_meta_event", return_value=True) as mock_meta:
        await worker.process_event("1000-0", payload)
        mock_meta.assert_not_called()
