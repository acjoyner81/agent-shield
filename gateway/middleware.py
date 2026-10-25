import re
import time
from typing import Callable, Iterable, Optional
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from gateway.telemetry import (
    emit_request_completed,
    emit_authz_failure,
    emit_rate_limit_exceeded,
    emit_token_usage,
)

# Infrastructure probes and schema routes are not tenant API traffic. Emitting a
# request.completed for them would stamp an "unknown"-tenant row onto the per
# tenant counters on every liveness check, so they are skipped.
#
# The telemetry surface is skipped too: it reads and rewrites the very stream and
# history this middleware appends to, so instrumenting it is a feedback loop that
# makes the stream harder to reason about. A replay is measured by what it moved,
# not by an event about the replay.
DEFAULT_EXCLUDED_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
    "/v1/telemetry",
    "/api/v1/telemetry",
)

_TRACEPARENT = re.compile(
    r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})",
    re.IGNORECASE,
)


class TelemetryMiddleware(BaseHTTPMiddleware):
    """Emit one ``agentshield.telemetry.request.completed`` per served request.

    The tenant is read from request state, which the auth dependencies bind from
    the verified principal. It is deliberately *not* read from a request header:
    ``X-Tenant-ID`` is not authoritative anywhere else in the gateway, so reading
    it here would let a caller tag their own telemetry as another tenant. A
    request that never resolved a principal is recorded as ``unknown``.

    Emission happens in a ``finally`` so the request is still recorded when the
    route raises, in which case the outcome is the 500 the error middleware sends.
    Telemetry is best effort and never changes the response.
    """

    def __init__(self, app, excluded_prefixes: Iterable[str] = DEFAULT_EXCLUDED_PREFIXES):
        super().__init__(app)
        self.excluded_prefixes = tuple(excluded_prefixes)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        trace_id, span_id = _trace_ids(request)
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            if not request.url.path.startswith(self.excluded_prefixes):
                self._emit(
                    request,
                    status_code,
                    (time.perf_counter() - start) * 1000,
                    trace_id,
                    span_id,
                )

    def _emit(
        self,
        request: Request,
        status_code: int,
        latency_ms: float,
        trace_id: str,
        span_id: str,
    ) -> None:
        try:
            emit_request_completed(
                tenant_id=getattr(request.state, "tenant_id", None) or "unknown",
                user_id=getattr(request.state, "user_id", None),
                trace_id=trace_id,
                span_id=span_id,
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                latency_ms=round(latency_ms, 3),
            )
        except Exception:
            # Telemetry must never change the caller's outcome.
            pass


def _trace_ids(request: Request) -> tuple[str, str]:
    """Use a valid W3C traceparent or create one for downstream calls."""
    traceparent = request.headers.get("traceparent")
    match = _TRACEPARENT.fullmatch(traceparent or "")
    if match and match.group(1) != "0" * 32 and match.group(2) != "0" * 16:
        trace_id, span_id, flags = (part.lower() for part in match.groups())
        traceparent = f"00-{trace_id}-{span_id}-{flags}"
    else:
        trace_id = uuid4().hex
        span_id = uuid4().hex[:16]
        traceparent = f"00-{trace_id}-{span_id}-01"

    if request.headers.get("traceparent") != traceparent:
        request.scope["headers"] = [
            (name, value)
            for name, value in request.scope["headers"]
            if name.lower() != b"traceparent"
        ] + [(b"traceparent", traceparent.encode("ascii"))]

    request.state.trace_id = trace_id
    request.state.span_id = span_id
    return trace_id, span_id


def track_authz_failure(
    request: Request,
    missing_scope: Optional[str] = None,
) -> str:
    return emit_authz_failure(
        tenant_id=getattr(request.state, "tenant_id", "unknown"),
        missing_scope=missing_scope,
        user_id=getattr(request.state, "user_id", None),
        trace_id=getattr(request.state, "trace_id", None),
        span_id=getattr(request.state, "span_id", None),
    )


def track_rate_limit_exceeded(
    request: Request,
    rate_limit_remaining: int = 0,
) -> str:
    return emit_rate_limit_exceeded(
        tenant_id=getattr(request.state, "tenant_id", "unknown"),
        rate_limit_remaining=rate_limit_remaining,
        user_id=getattr(request.state, "user_id", None),
        trace_id=getattr(request.state, "trace_id", None),
        span_id=getattr(request.state, "span_id", None),
    )


def track_token_usage(
    request: Request,
    input_tokens: int,
    output_tokens: int,
    model: str,
) -> str:
    return emit_token_usage(
        tenant_id=getattr(request.state, "tenant_id", "unknown"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
        user_id=getattr(request.state, "user_id", None),
        trace_id=getattr(request.state, "trace_id", None),
        span_id=getattr(request.state, "span_id", None),
    )
