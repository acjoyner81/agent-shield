import json
import unittest
from pydantic import ValidationError

from gateway.telemetry import (
    EventEnvelope,
    HttpData,
    TokenData,
    SecurityData,
    BillingData,
    TelemetryEventEmitter,
    emit_request_completed,
    emit_token_usage,
    emit_authz_failure,
    emit_rate_limit_exceeded,
    emit_billing_subscription_changed,
    log_telemetry,
)


class TestEventEnvelopeValidation(unittest.TestCase):
    def test_valid_envelope(self):
        event = EventEnvelope(
            type="agentshield.telemetry.request.completed",
            tenant_id="tenant_alpha",
            user_id="usr_123",
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            span_id="00f067aa0ba902b7",
            data={"method": "POST", "path": "/v1/chat"},
        )
        self.assertEqual(event.specversion, "1.0")
        self.assertTrue(event.event_id.startswith("evt_"))
        self.assertEqual(len(event.event_id), 36)
        self.assertEqual(event.source, "agentshield-gateway-python")
        self.assertEqual(event.tenant_id, "tenant_alpha")

    def test_envelope_with_custom_source(self):
        event = EventEnvelope(
            type="agentshield.token.usage",
            tenant_id="tenant_alpha",
            source="agentshield-gateway-java",
            data={},
        )
        self.assertEqual(event.source, "agentshield-gateway-java")

    def test_invalid_event_type_raises(self):
        with self.assertRaises(ValidationError):
            EventEnvelope(
                type="invalid_type",
                tenant_id="tenant_alpha",
                data={},
            )

    def test_invalid_event_id_raises(self):
        with self.assertRaises(ValidationError):
            EventEnvelope(
                type="agentshield.telemetry.request.completed",
                tenant_id="tenant_alpha",
                event_id="not-a-valid-id",
                data={},
            )

    def test_event_id_uuid_format(self):
        event = EventEnvelope(
            type="agentshield.telemetry.request.completed",
            tenant_id="tenant_alpha",
            data={},
        )
        self.assertTrue(event.event_id.startswith("evt_"))
        self.assertEqual(len(event.event_id), 36)

    def test_envelope_model_dump(self):
        event = EventEnvelope(
            type="agentshield.telemetry.request.completed",
            tenant_id="tenant_alpha",
            data={"key": "value"},
        )
        d = event.model_dump()
        self.assertEqual(d["specversion"], "1.0")
        self.assertEqual(d["type"], "agentshield.telemetry.request.completed")
        self.assertEqual(d["data"], {"key": "value"})

    def test_envelope_json_serializable(self):
        event = EventEnvelope(
            type="agentshield.telemetry.request.completed",
            tenant_id="tenant_alpha",
            data=HttpData(method="POST", path="/v1/chat", status_code=200, latency_ms=142).model_dump(),
        )
        raw = event.model_dump_json()
        parsed = json.loads(raw)
        self.assertEqual(parsed["specversion"], "1.0")
        self.assertEqual(parsed["data"]["status_code"], 200)


class TestHttpData(unittest.TestCase):
    def test_http_data_creation(self):
        data = HttpData(method="POST", path="/v1/billing/checkout", status_code=200, latency_ms=142)
        self.assertEqual(data.method, "POST")
        self.assertEqual(data.path, "/v1/billing/checkout")
        self.assertEqual(data.status_code, 200)
        self.assertEqual(data.latency_ms, 142)

    def test_http_data_model_dump(self):
        data = HttpData(method="GET", path="/health", status_code=200, latency_ms=5)
        d = data.model_dump()
        self.assertEqual(d, {"method": "GET", "path": "/health", "status_code": 200, "latency_ms": 5})


class TestTokenData(unittest.TestCase):
    def test_token_data_creation(self):
        data = TokenData(input=512, output=128, total=640, model="gemini-1.5-pro")
        self.assertEqual(data.input, 512)
        self.assertEqual(data.output, 128)
        self.assertEqual(data.total, 640)
        self.assertEqual(data.model, "gemini-1.5-pro")

    def test_token_total_is_sum(self):
        data = TokenData(input=100, output=50, total=150, model="gpt-4o")
        self.assertEqual(data.total, data.input + data.output)


class TestSecurityData(unittest.TestCase):
    def test_security_data_creation(self):
        data = SecurityData(rbac_passed=True, rate_limit_remaining=498, tripwire_flagged=False)
        self.assertTrue(data.rbac_passed)
        self.assertEqual(data.rate_limit_remaining, 498)
        self.assertFalse(data.tripwire_flagged)

    def test_security_data_all_false(self):
        data = SecurityData(rbac_passed=False, rate_limit_remaining=0, tripwire_flagged=True)
        self.assertFalse(data.rbac_passed)
        self.assertEqual(data.rate_limit_remaining, 0)
        self.assertTrue(data.tripwire_flagged)


class TestBillingData(unittest.TestCase):
    def test_billing_data_creation(self):
        data = BillingData(action="created", stripe_customer_id="cus_123", tier="pro", status="active")
        self.assertEqual(data.action, "created")
        self.assertEqual(data.stripe_customer_id, "cus_123")
        self.assertEqual(data.tier, "pro")
        self.assertEqual(data.status, "active")

    def test_billing_data_optional_fields(self):
        data = BillingData(action="deleted")
        self.assertEqual(data.action, "deleted")
        self.assertIsNone(data.stripe_customer_id)
        self.assertIsNone(data.tier)
        self.assertIsNone(data.status)


class TestTelemetryEmitter(unittest.TestCase):
    def test_emit_request_completed(self):
        raw = emit_request_completed(
            tenant_id="tenant_alpha",
            method="POST",
            path="/v1/chat/completions",
            status_code=200,
            latency_ms=142,
            user_id="usr_123",
            trace_id="trace_123",
            span_id="span_456",
        )
        event = json.loads(raw)
        self.assertEqual(event["type"], "agentshield.telemetry.request.completed")
        self.assertEqual(event["data"]["method"], "POST")
        self.assertEqual(event["data"]["path"], "/v1/chat/completions")
        self.assertEqual(event["data"]["status_code"], 200)
        self.assertEqual(event["data"]["latency_ms"], 142)

    def test_emit_token_usage(self):
        raw = emit_token_usage(
            tenant_id="tenant_alpha",
            input_tokens=512,
            output_tokens=128,
            model="gemini-1.5-pro",
            user_id="usr_123",
            trace_id="trace_123",
            span_id="span_456",
        )
        event = json.loads(raw)
        self.assertEqual(event["type"], "agentshield.token.usage")
        self.assertEqual(event["data"]["input"], 512)
        self.assertEqual(event["data"]["output"], 128)
        self.assertEqual(event["data"]["total"], 640)
        self.assertEqual(event["data"]["model"], "gemini-1.5-pro")

    def test_emit_authz_failure(self):
        raw = emit_authz_failure(
            tenant_id="tenant_alpha",
            missing_scope="tools:execute",
            user_id="usr_123",
            trace_id="trace_123",
            span_id="span_456",
        )
        event = json.loads(raw)
        self.assertEqual(event["type"], "agentshield.security.authz_failure")
        self.assertFalse(event["data"]["rbac_passed"])
        self.assertEqual(event["data"]["rate_limit_remaining"], 0)
        self.assertFalse(event["data"]["tripwire_flagged"])

    def test_emit_rate_limit_exceeded(self):
        raw = emit_rate_limit_exceeded(
            tenant_id="tenant_alpha",
            rate_limit_remaining=0,
            user_id="usr_123",
            trace_id="trace_123",
            span_id="span_456",
        )
        event = json.loads(raw)
        self.assertEqual(event["type"], "agentshield.security.rate_limit_exceeded")
        self.assertTrue(event["data"]["rbac_passed"])
        self.assertEqual(event["data"]["rate_limit_remaining"], 0)

    def test_emit_billing_subscription_changed(self):
        raw = emit_billing_subscription_changed(
            tenant_id="tenant_alpha",
            action="created",
            stripe_customer_id="cus_123",
            tier="pro",
            status="active",
            user_id="usr_123",
            trace_id="trace_123",
            span_id="span_456",
        )
        event = json.loads(raw)
        self.assertEqual(event["type"], "agentshield.billing.subscription_changed")
        self.assertEqual(event["data"]["action"], "created")
        self.assertEqual(event["data"]["stripe_customer_id"], "cus_123")
        self.assertEqual(event["data"]["tier"], "pro")
        self.assertEqual(event["data"]["status"], "active")

    def test_all_event_types_are_valid(self):
        valid_types = [
            "agentshield.telemetry.request.completed",
            "agentshield.token.usage",
            "agentshield.security.authz_failure",
            "agentshield.security.rate_limit_exceeded",
            "agentshield.billing.subscription_changed",
        ]
        for event_type in valid_types:
            event = EventEnvelope(
                type=event_type,
                tenant_id="tenant_alpha",
                data={},
            )
            self.assertEqual(event.type, event_type)

    def test_non_matching_type_raises(self):
        with self.assertRaises(ValidationError):
            EventEnvelope(
                type="some.other.event",
                tenant_id="tenant_alpha",
                data={},
            )


class TestBackwardCompatibility(unittest.TestCase):
    def test_log_telemetry_alias_exists(self):
        self.assertTrue(hasattr(TelemetryEventEmitter, "emit"))
        self.assertTrue(callable(TelemetryEventEmitter.emit))

    def test_module_level_functions_exist(self):
        self.assertTrue(callable(emit_request_completed))
        self.assertTrue(callable(emit_token_usage))
        self.assertTrue(callable(emit_authz_failure))
        self.assertTrue(callable(emit_rate_limit_exceeded))
        self.assertTrue(callable(emit_billing_subscription_changed))
        self.assertTrue(callable(log_telemetry))


if __name__ == "__main__":
    unittest.main()
