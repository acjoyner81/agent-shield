import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("agentshield.telemetry")

# How many rows the portal's audit stream retains. Matches the trim the POST
# /v1/telemetry/logs ingest path applies, so both writers agree on the cap.
HISTORY_LIMIT = 100

# How many un-shipped events the queue retains.
#
# The stream used to grow without bound. Nothing in this file ever acknowledged
# or trimmed an entry, so a gateway that outran Splunk, or one shipping to a
# collector that was down for days, grew a queue that could not be inspected, and
# whose only visible symptom was memory. An approximate cap is the right trade
# here: trimming exactly costs an O(N) delete per write, and the events dropped
# by an approximate trim are the ones a consumer that has fallen far enough
# behind has not read anyway.
QUEUE_MAXLEN = 100_000

VALID_EVENT_TYPES = {
    "agentshield.telemetry.request.completed",
    "agentshield.token.usage",
    "agentshield.security.authz_failure",
    "agentshield.security.rate_limit_exceeded",
    "agentshield.security.key_rotation",
    "agentshield.billing.subscription_changed",
}


class EventType(str, Enum):
    REQUEST_COMPLETED = "agentshield.telemetry.request.completed"
    TOKEN_USAGE = "agentshield.token.usage"
    AUTHZ_FAILURE = "agentshield.security.authz_failure"
    RATE_LIMIT_EXCEEDED = "agentshield.security.rate_limit_exceeded"
    KEY_ROTATION = "agentshield.security.key_rotation"
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


class KeyRotationData(BaseModel):
    key_id: str
    action: str
    status: str
    version: Optional[int] = None
    rotated_at: Optional[str] = None
    superseded_by: Optional[str] = None


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


# The audit stream the portal renders is a flat projection of these CloudEvents,
# read by gateway.main.get_telemetry_logs. A status code and a latency belong
# only to events that correspond to an HTTP outcome: a key rotation is not a
# response, and defaulting it to 200/0 made the Status and Latency columns
# assert a successful request that never happened.
_STATUS_BY_TYPE = {
    EventType.RATE_LIMIT_EXCEEDED.value: 429,
    EventType.AUTHZ_FAILURE.value: 403,
}


def _summarize(event_type: str, data: Dict[str, Any]) -> str:
    """One human-readable line per event type, for the stream's Message column."""
    if event_type == EventType.REQUEST_COMPLETED.value:
        return f"{data.get('method', '')} {data.get('path', '')}".strip()
    if event_type == EventType.TOKEN_USAGE.value:
        return f"{data.get('total', 0)} tokens via {data.get('model', 'unknown model')}"
    if event_type == EventType.KEY_ROTATION.value:
        action = data.get("action", "changed")
        return f"API key {data.get('key_id', 'unknown')} {action} ({data.get('status', 'unknown')})"
    if event_type == EventType.RATE_LIMIT_EXCEEDED.value:
        return "Rate limit exceeded; request dropped"
    if event_type == EventType.AUTHZ_FAILURE.value:
        return "Authorization failed; request denied"
    if event_type == EventType.SUBSCRIPTION_CHANGED.value:
        return f"Subscription {data.get('action', 'changed')}"
    return event_type


def _history_row(envelope: EventEnvelope) -> Dict[str, Any]:
    """Flatten an envelope into the row shape the portal's audit stream reads."""
    data = envelope.data or {}
    row: Dict[str, Any] = {
        "event_id": envelope.event_id,
        "tenant_id": envelope.tenant_id,
        "type": envelope.type,
        "timestamp": envelope.timestamp,
        "message": _summarize(envelope.type, data),
        "trace_id": envelope.trace_id or "",
    }

    if envelope.type == EventType.REQUEST_COMPLETED.value:
        row["status_code"] = data.get("status_code")
        row["latency_ms"] = data.get("latency_ms")
    elif envelope.type in _STATUS_BY_TYPE:
        row["status_code"] = _STATUS_BY_TYPE[envelope.type]

    if envelope.type == EventType.TOKEN_USAGE.value:
        # Carried, not priced. Spec 0011 recomputes cost from the rate card on
        # every read so an edited price card changes the next response.
        row["model"] = data.get("model")
        row["total_tokens"] = data.get("total")

    return row


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

    # If no client passed, attempt to use global gateway redis client if available
    client = redis_client
    if client is None:
        try:
            from gateway.rate_limit import get_redis_client
            client = get_redis_client()
        except Exception:
            pass

    if client:
        try:
            # Capped at the producer, because the producer is the only party that
            # knows whether the pipeline is keeping up.
            client.xadd("telemetry:queue", {"payload": payload}, maxlen=QUEUE_MAXLEN, approximate=True)
        except Exception as e:
            logger.error(f"Failed to push to Redis stream: {e}")

        # The portal's audit stream reads telemetry_history, and the aggregator
        # only ever ships to Splunk, so nothing downstream ever wrote it. Every
        # gateway event therefore missed the page the user is looking at, and it
        # did so silently: the route returned an honest empty list. The local
        # read model belongs to the producer, written here beside the enqueue,
        # so the stream renders whether or not the aggregator and Splunk are up.
        try:
            client.lpush("telemetry_history", json.dumps(_history_row(envelope)))
            client.ltrim("telemetry_history", 0, HISTORY_LIMIT - 1)
        except Exception as e:
            logger.error(f"Failed to append to telemetry history: {e}")
    else:
        logger.debug(f"Telemetry payload (unbuffered): {payload}")

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
    redis_client: Optional[Any] = None,
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
        redis_client=redis_client,
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


def emit_key_rotation(
    tenant_id: str,
    key_id: str,
    action: str,
    status: str,
    version: Optional[int] = None,
    rotated_at: Optional[str] = None,
    superseded_by: Optional[str] = None,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
    redis_client: Optional[Any] = None,
) -> str:
    data = KeyRotationData(
        key_id=key_id,
        action=action,
        status=status,
        version=version,
        rotated_at=rotated_at,
        superseded_by=superseded_by,
    ).model_dump()
    return emit_event(
        tenant_id=tenant_id,
        event_type=EventType.KEY_ROTATION.value,
        data=data,
        user_id=user_id,
        trace_id=trace_id,
        span_id=span_id,
        redis_client=redis_client,
    )


log_telemetry = emit_event
