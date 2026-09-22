import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("agentshield.telemetry")

VALID_EVENT_TYPES = {
    "agentshield.telemetry.request.completed",
    "agentshield.token.usage",
    "agentshield.security.authz_failure",
    "agentshield.security.rate_limit_exceeded",
    "agentshield.billing.subscription_changed",
}


class EventType(str, Enum):
    REQUEST_COMPLETED = "agentshield.telemetry.request.completed"
    TOKEN_USAGE = "agentshield.token.usage"
    AUTHZ_FAILURE = "agentshield.security.authz_failure"
    RATE_LIMIT_EXCEEDED = "agentshield.security.rate_limit_exceeded"
    SUBSCRIPTION_CHANGED = "agentshield.billing.subscription_changed"


class HttpData(BaseModel):
    method: str
    path: str
    status_code: int
    latency_ms: float


class TokenData(BaseModel):
    input: int
    output: int
    total: int
    model: str


class SecurityData(BaseModel):
    rbac_passed: bool
    rate_limit_remaining: int
    tripwire_flagged: bool


class BillingData(BaseModel):
    action: str
    stripe_customer_id: Optional[str] = None
    tier: Optional[str] = None
    status: Optional[str] = None


class EventEnvelope(BaseModel):
    specversion: str = "1.0"
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex}")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = "agentshield-gateway-python"
    type: str
    tenant_id: str
    user_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_id")
    def validate_event_id(cls, v: str) -> str:
        if not v.startswith("evt_") or len(v) != 36:
            raise ValueError("event_id must start with 'evt_' followed by 32 hex characters")
        return v

    @field_validator("type")
    def validate_type(cls, v: str) -> str:
        if v not in VALID_EVENT_TYPES:
            raise ValueError(f"Invalid event type '{v}'. Must be one of {VALID_EVENT_TYPES}")
        return v


def emit_event(
    tenant_id: str,
    event_type: str,
    data: Dict[str, Any],
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
    source: str = "agentshield-gateway-python",
    redis_client: Optional[Any] = None,
) -> str:
    envelope = EventEnvelope(
        specversion="1.0",
        source=source,
        type=event_type,
        tenant_id=tenant_id,
        user_id=user_id,
        trace_id=trace_id,
        span_id=span_id,
        data=data,
    )

    payload = envelope.model_dump_json()

    if redis_client:
        try:
            redis_client.xadd("telemetry:queue", {"payload": payload})
        except Exception as e:
            logger.error(f"Failed to push to Redis stream: {e}")
            print(payload)
    else:
        print(payload)

    return payload


class TelemetryEventEmitter:
    @staticmethod
    def emit(
        tenant_id: str,
        event_type: str,
        data: Dict[str, Any],
        user_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        source: str = "agentshield-gateway-python",
        redis_client: Optional[Any] = None,
    ) -> str:
        return emit_event(
            tenant_id=tenant_id,
            event_type=event_type,
            data=data,
            user_id=user_id,
            trace_id=trace_id,
            span_id=span_id,
            source=source,
            redis_client=redis_client,
        )


def emit_request_completed(
    tenant_id: str,
    method: str,
    path: str,
    status_code: int,
    latency_ms: float,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
) -> str:
    data = HttpData(method=method, path=path, status_code=status_code, latency_ms=latency_ms).model_dump()
    return emit_event(
        tenant_id=tenant_id,
        event_type=EventType.REQUEST_COMPLETED.value,
        data=data,
        user_id=user_id,
        trace_id=trace_id,
        span_id=span_id,
    )


def emit_token_usage(
    tenant_id: str,
    input_tokens: int,
    output_tokens: int,
    model: str,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
) -> str:
    data = TokenData(
        input=input_tokens,
        output=output_tokens,
        total=input_tokens + output_tokens,
        model=model,
    ).model_dump()
    return emit_event(
        tenant_id=tenant_id,
        event_type=EventType.TOKEN_USAGE.value,
        data=data,
        user_id=user_id,
        trace_id=trace_id,
        span_id=span_id,
    )


def emit_authz_failure(
    tenant_id: str,
    missing_scope: Optional[str] = None,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
) -> str:
    data = SecurityData(rbac_passed=False, rate_limit_remaining=0, tripwire_flagged=False).model_dump()
    return emit_event(
        tenant_id=tenant_id,
        event_type=EventType.AUTHZ_FAILURE.value,
        data=data,
        user_id=user_id,
        trace_id=trace_id,
        span_id=span_id,
    )


def emit_rate_limit_exceeded(
    tenant_id: str,
    rate_limit_remaining: int = 0,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
) -> str:
    data = SecurityData(rbac_passed=True, rate_limit_remaining=rate_limit_remaining, tripwire_flagged=False).model_dump()
    return emit_event(
        tenant_id=tenant_id,
        event_type=EventType.RATE_LIMIT_EXCEEDED.value,
        data=data,
        user_id=user_id,
        trace_id=trace_id,
        span_id=span_id,
    )


def emit_billing_subscription_changed(
    tenant_id: str,
    action: str,
    stripe_customer_id: Optional[str] = None,
    tier: Optional[str] = None,
    status: Optional[str] = None,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
) -> str:
    data = BillingData(
        action=action,
        stripe_customer_id=stripe_customer_id,
        tier=tier,
        status=status,
    ).model_dump()
    return emit_event(
        tenant_id=tenant_id,
        event_type=EventType.SUBSCRIPTION_CHANGED.value,
        data=data,
        user_id=user_id,
        trace_id=trace_id,
        span_id=span_id,
    )


log_telemetry = emit_event
