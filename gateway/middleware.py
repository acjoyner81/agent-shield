import time
from typing import Callable, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from gateway.telemetry import (
    emit_request_completed,
    emit_authz_failure,
    emit_rate_limit_exceeded,
    emit_token_usage,
)


class TelemetryMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        tenant_header_name: str = "X-Tenant-ID",
        user_header_name: str = "X-User-ID",
        trace_header_name: str = "X-Trace-ID",
        span_header_name: str = "X-Span-ID",
    ):
        super().__init__(app)
        self.tenant_header_name = tenant_header_name
        self.user_header_name = user_header_name
        self.trace_header_name = trace_header_name
        self.span_header_name = span_header_name

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.perf_counter()

        tenant_id = request.headers.get(self.tenant_header_name, "anonymous")
        user_id = request.headers.get(self.user_header_name)
        trace_id = request.headers.get(self.trace_header_name)
        span_id = request.headers.get(self.span_header_name)

        request.state.tenant_id = tenant_id
        request.state.user_id = user_id
        request.state.trace_id = trace_id
        request.state.span_id = span_id

        response = await call_next(request)

        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)

        emit_request_completed(
            tenant_id=tenant_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            latency_ms=latency_ms,
            user_id=user_id,
            trace_id=trace_id,
            span_id=span_id,
        )

        return response


def track_authz_failure(
    request: Request,
    missing_scope: Optional[str] = None,
) -> str:
    return emit_authz_failure(
        tenant_id=getattr(request.state, "tenant_id", "anonymous"),
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
        tenant_id=getattr(request.state, "tenant_id", "anonymous"),
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
        tenant_id=getattr(request.state, "tenant_id", "anonymous"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
        user_id=getattr(request.state, "user_id", None),
        trace_id=getattr(request.state, "trace_id", None),
        span_id=getattr(request.state, "span_id", None),
    )
