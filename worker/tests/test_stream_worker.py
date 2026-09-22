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
