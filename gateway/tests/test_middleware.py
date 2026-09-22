import unittest
from unittest.mock import patch
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from gateway.middleware import (
    TelemetryMiddleware,
    track_authz_failure,
    track_rate_limit_exceeded,
    track_token_usage,
)


class TestTelemetryMiddleware(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.add_middleware(TelemetryMiddleware)

        @self.app.get("/v1/chat")
        async def chat_endpoint(request: Request):
            return {"message": "ok"}

        @self.app.post("/v1/completions")
        async def completions_endpoint(request: Request):
            track_token_usage(
                request=request,
                input_tokens=100,
                output_tokens=50,
                model="gpt-4o",
            )
            return {"status": "success"}

        @self.app.get("/v1/protected")
        async def protected_endpoint(request: Request):
            track_authz_failure(request=request, missing_scope="admin")
            return {"error": "unauthorized"}

        @self.app.get("/v1/rate-limited")
        async def rate_limited_endpoint(request: Request):
            track_rate_limit_exceeded(request=request, rate_limit_remaining=0)
            return {"error": "too many requests"}

        self.client = TestClient(self.app)

    @patch("gateway.middleware.emit_request_completed")
    def test_middleware_emits_request_completed(self, mock_emit):
        headers = {
            "X-Tenant-ID": "tenant_test",
            "X-User-ID": "usr_999",
            "X-Trace-ID": "tr_12345",
            "X-Span-ID": "sp_67890",
        }
        res = self.client.get("/v1/chat", headers=headers)
        self.assertEqual(res.status_code, 200)

        mock_emit.assert_called_once()
        kwargs = mock_emit.call_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "tenant_test")
        self.assertEqual(kwargs["method"], "GET")
        self.assertEqual(kwargs["path"], "/v1/chat")
        self.assertEqual(kwargs["status_code"], 200)
        self.assertEqual(kwargs["user_id"], "usr_999")
        self.assertEqual(kwargs["trace_id"], "tr_12345")
        self.assertEqual(kwargs["span_id"], "sp_67890")

    @patch("gateway.middleware.emit_token_usage")
    def test_track_token_usage_helper(self, mock_emit_token):
        headers = {"X-Tenant-ID": "tenant_llm", "X-User-ID": "usr_llm"}
        res = self.client.post("/v1/completions", headers=headers)
        self.assertEqual(res.status_code, 200)

        mock_emit_token.assert_called_once()
        kwargs = mock_emit_token.call_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "tenant_llm")
        self.assertEqual(kwargs["input_tokens"], 100)
        self.assertEqual(kwargs["output_tokens"], 50)
        self.assertEqual(kwargs["model"], "gpt-4o")

    @patch("gateway.middleware.emit_authz_failure")
    def test_track_authz_failure_helper(self, mock_emit_authz):
        headers = {"X-Tenant-ID": "tenant_sec"}
        self.client.get("/v1/protected", headers=headers)

        mock_emit_authz.assert_called_once()
        kwargs = mock_emit_authz.call_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "tenant_sec")
        self.assertEqual(kwargs["missing_scope"], "admin")

    @patch("gateway.middleware.emit_rate_limit_exceeded")
    def test_track_rate_limit_helper(self, mock_emit_rate):
        headers = {"X-Tenant-ID": "tenant_rate"}
        self.client.get("/v1/rate-limited", headers=headers)

        mock_emit_rate.assert_called_once()
        kwargs = mock_emit_rate.call_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "tenant_rate")
        self.assertEqual(kwargs["rate_limit_remaining"], 0)


if __name__ == "__main__":
    unittest.main()
