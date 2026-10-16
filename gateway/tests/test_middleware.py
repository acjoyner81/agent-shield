"""Spec 0008: every served request emits one request.completed.

The tenant is taken from the principal the auth dependencies bound to request
state, never from a caller-supplied header, so a caller cannot tag telemetry as
another tenant. Infrastructure probes are skipped.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from gateway.middleware import (
    TelemetryMiddleware,
    track_authz_failure,
    track_rate_limit_exceeded,
    track_token_usage,
)

TRACEPARENT = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"


def build_app() -> TestClient:
    app = FastAPI()
    app.add_middleware(TelemetryMiddleware)

    @app.get("/v1/chat")
    async def chat(request: Request):
        request.state.tenant_id = "tenant_alpha"
        request.state.user_id = "usr_999"
        return {"status": "success"}

    @app.post("/v1/completions")
    async def completions(request: Request):
        request.state.tenant_id = "tenant_llm"
        request.state.user_id = "usr_llm"
        track_token_usage(request=request, input_tokens=100, output_tokens=50, model="gpt-4o")
        return {"status": "success"}

    @app.get("/v1/protected")
    async def protected(request: Request):
        request.state.tenant_id = "tenant_sec"
        track_authz_failure(request=request, missing_scope="admin")
        return {"error": "unauthorized"}

    @app.get("/v1/rate-limited")
    async def rate_limited(request: Request):
        request.state.tenant_id = "tenant_rate"
        track_rate_limit_exceeded(request=request, rate_limit_remaining=0)
        return {"error": "too many requests"}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return TestClient(app)


@pytest.fixture(autouse=True)
def request_completed():
    with patch("gateway.middleware.emit_request_completed") as mock_emit:
        yield mock_emit


def test_emits_request_completed_from_principal_state(request_completed):
    res = build_app().get("/v1/chat", headers={"traceparent": TRACEPARENT})

    assert res.status_code == 200
    request_completed.assert_called_once()
    kwargs = request_completed.call_args.kwargs
    assert kwargs["tenant_id"] == "tenant_alpha"
    assert kwargs["user_id"] == "usr_999"
    assert kwargs["method"] == "GET"
    assert kwargs["path"] == "/v1/chat"
    assert kwargs["status_code"] == 200
    assert kwargs["trace_id"] == "a" * 32
    assert kwargs["span_id"] == "a" * 16
    assert kwargs["latency_ms"] >= 0


def test_spoofed_tenant_header_is_ignored(request_completed):
    build_app().get("/v1/chat", headers={"X-Tenant-ID": "tenant_evil"})

    assert request_completed.call_args.kwargs["tenant_id"] == "tenant_alpha"


def test_unresolved_principal_is_recorded_as_unknown(request_completed):
    app = FastAPI()
    app.add_middleware(TelemetryMiddleware)

    @app.get("/v1/open")
    async def open_route():
        return {"ok": True}

    TestClient(app).get("/v1/open")

    assert request_completed.call_args.kwargs["tenant_id"] == "unknown"


def test_health_probe_is_not_emitted(request_completed):
    res = build_app().get("/health")

    assert res.status_code == 200
    request_completed.assert_not_called()


def test_failing_request_still_emits(request_completed):
    app = FastAPI()
    app.add_middleware(TelemetryMiddleware)

    @app.get("/v1/boom")
    async def boom():
        raise RuntimeError("boom")

    res = TestClient(app, raise_server_exceptions=False).get("/v1/boom")

    assert res.status_code == 500
    assert request_completed.call_args.kwargs["status_code"] == 500
    assert request_completed.call_args.kwargs["tenant_id"] == "unknown"


@patch("gateway.middleware.emit_token_usage")
def test_track_token_usage_helper(mock_emit_token):
    build_app().post("/v1/completions")

    kwargs = mock_emit_token.call_args.kwargs
    assert kwargs["tenant_id"] == "tenant_llm"
    assert kwargs["input_tokens"] == 100
    assert kwargs["output_tokens"] == 50
    assert kwargs["model"] == "gpt-4o"


@patch("gateway.middleware.emit_authz_failure")
def test_track_authz_failure_helper(mock_emit_authz):
    build_app().get("/v1/protected")

    kwargs = mock_emit_authz.call_args.kwargs
    assert kwargs["tenant_id"] == "tenant_sec"
    assert kwargs["missing_scope"] == "admin"


@patch("gateway.middleware.emit_rate_limit_exceeded")
def test_track_rate_limit_helper(mock_emit_rate):
    build_app().get("/v1/rate-limited")

    kwargs = mock_emit_rate.call_args.kwargs
    assert kwargs["tenant_id"] == "tenant_rate"
    assert kwargs["rate_limit_remaining"] == 0
