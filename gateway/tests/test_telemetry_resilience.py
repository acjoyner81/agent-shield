import pytest
from unittest.mock import patch, MagicMock
from gateway.telemetry import emit_event, EventType

class TestTelemetryResilience:
    @patch("gateway.rate_limit.get_redis_client")
    def test_emit_event_handles_redis_failure_gracefully(self, mock_get_redis_client):
        # Setup a mock Redis client whose xadd method raises a ConnectionError
        mock_redis_instance = MagicMock()
        mock_redis_instance.xadd.side_effect = ConnectionError("Redis down / DLQ simulation")
        mock_get_redis_client.return_value = mock_redis_instance

        # Attempting to emit an event should catch the exception internally and return the payload normally,
        # ensuring it never propagates an unhandled exception to calling code or request handlers.
        payload = emit_event(
            tenant_id="tenant_resilience_test",
            event_type=EventType.REQUEST_COMPLETED.value,
            data={"method": "POST", "path": "/v1/chat/completions", "status_code": 200, "latency_ms": 45.2}
        )

        # Verify the payload was still successfully constructed and returned
        assert payload is not None
        assert "tenant_resilience_test" in payload
        
        # Verify xadd was indeed called and caught
        mock_redis_instance.xadd.assert_called_once()