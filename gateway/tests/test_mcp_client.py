"""Slice 3 tests for the gateway's MCP streamable client (spec 0015.2).

Covers the machine-token cache and the streamable tools/call handshake without
touching the network. JWT verification on mcp-server is Java-side and covered by
the mcp-server suite; here the point is that the gateway mints its own token,
never forwards the caller's, and maps the MCP content array back to the existing
`{status, result, trace_id}` envelope.
"""

from collections import namedtuple

import httpx
import pytest

from gateway import mcp_client

Record = namedtuple("Record", "url body headers")


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_body
        self.headers = dict(headers or {})
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://mcp-server/mcp/v1")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=request, response=response
            )


@pytest.fixture(autouse=True)
def _reset_client_caches(monkeypatch):
    monkeypatch.setattr(mcp_client.settings, "auth0_m2m_client_id", "test-m2m-client")
    monkeypatch.setattr(mcp_client.settings, "auth0_m2m_client_secret", "test-m2m-secret")
    mcp_client.reset_caches()
    yield
    mcp_client.reset_caches()


def _fake_transport(monkeypatch, handler):
    """Install a fake httpx.AsyncClient and return the recorded calls."""
    calls = []

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None, **kwargs):
            calls.append(Record(url, json, dict(headers or {})))
            return handler(url, json, headers or {})

    monkeypatch.setattr(mcp_client.httpx, "AsyncClient", _Client)
    return calls


def _standard_handler(tool_result, session_id="sess-1"):
    def handler(url, body, headers):
        if url.endswith("/oauth/token"):
            return _FakeResponse(
                json_body={"access_token": "m2m-token", "expires_in": 3600}
            )
        method = (body or {}).get("method")
        if method == "initialize":
            return _FakeResponse(
                json_body={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"protocolVersion": mcp_client.MCP_PROTOCOL_VERSION},
                },
                headers={"mcp-session-id": session_id, "content-type": "application/json"},
            )
        if method == "notifications/initialized":
            return _FakeResponse(status_code=202)
        if method == "tools/call":
            return _FakeResponse(
                json_body={"jsonrpc": "2.0", "id": 2, "result": tool_result}
            )
        raise AssertionError(f"unexpected method: {method}")

    return handler


@pytest.mark.asyncio
async def test_missing_credentials_fail_fast(monkeypatch):
    monkeypatch.setattr(mcp_client.settings, "auth0_m2m_client_id", "")
    monkeypatch.setattr(mcp_client.settings, "auth0_m2m_client_secret", "")

    with pytest.raises(mcp_client.McpConfigurationError):
        await mcp_client.get_access_token()


@pytest.mark.asyncio
async def test_token_is_cached_across_calls(monkeypatch):
    calls = _fake_transport(monkeypatch, _standard_handler({"content": []}))

    first = await mcp_client.get_access_token()
    second = await mcp_client.get_access_token()

    assert first == second == "m2m-token"
    assert sum(1 for c in calls if c.url.endswith("/oauth/token")) == 1


@pytest.mark.asyncio
async def test_force_refresh_reissues_the_token(monkeypatch):
    calls = _fake_transport(monkeypatch, _standard_handler({"content": []}))

    await mcp_client.get_access_token()
    await mcp_client.get_access_token(force_refresh=True)

    assert sum(1 for c in calls if c.url.endswith("/oauth/token")) == 2


@pytest.mark.asyncio
async def test_call_runs_handshake_and_maps_content(monkeypatch):
    result_body = {"content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]}
    calls = _fake_transport(monkeypatch, _standard_handler(result_body))

    result = await mcp_client.call_mcp_tool(
        "echoTool", {"message": "hello"}, "tenant_alpha", "00-trace-span-01"
    )

    assert mcp_client.tool_result_text(result) == "hello\nworld"

    initialize = next(c for c in calls if (c.body or {}).get("method") == "initialize")
    notification = next(
        c for c in calls if (c.body or {}).get("method") == "notifications/initialized"
    )
    tool_call = next(c for c in calls if (c.body or {}).get("method") == "tools/call")

    # The session id comes back on initialize and is echoed on later requests.
    assert "Mcp-Session-Id" not in initialize.headers
    assert notification.headers["Mcp-Session-Id"] == "sess-1"
    assert tool_call.headers["Mcp-Session-Id"] == "sess-1"

    # Only the machine token travels; the caller's identity is a label only.
    assert tool_call.headers["Authorization"] == "Bearer m2m-token"
    assert tool_call.headers["X-Tenant-ID"] == "tenant_alpha"
    assert tool_call.headers["traceparent"] == "00-trace-span-01"


@pytest.mark.asyncio
async def test_401_refreshes_token_and_retries_once(monkeypatch):
    state = {"tokens": 0, "tool_calls": 0}

    def handler(url, body, headers):
        if url.endswith("/oauth/token"):
            state["tokens"] += 1
            return _FakeResponse(
                json_body={"access_token": f"m2m-token-{state['tokens']}", "expires_in": 3600}
            )
        method = (body or {}).get("method")
        if method == "initialize":
            return _FakeResponse(
                json_body={"jsonrpc": "2.0", "id": 1, "result": {}},
                headers={"mcp-session-id": "sess-1", "content-type": "application/json"},
            )
        if method == "notifications/initialized":
            return _FakeResponse(status_code=202)
        if method == "tools/call":
            state["tool_calls"] += 1
            if state["tool_calls"] == 1:
                return _FakeResponse(status_code=401)
            return _FakeResponse(
                json_body={"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "ok"}]}}
            )
        raise AssertionError(method)

    _fake_transport(monkeypatch, handler)

    result = await mcp_client.call_mcp_tool("echoTool", {}, "tenant_alpha", "00-a-b-01")

    assert mcp_client.tool_result_text(result) == "ok"
    assert state["tokens"] == 2  # initial mint + forced refresh
    assert state["tool_calls"] == 2


@pytest.mark.asyncio
async def test_dropped_session_is_reinitialized_once(monkeypatch):
    state = {"tool_calls": 0, "initializes": 0}

    def handler(url, body, headers):
        if url.endswith("/oauth/token"):
            return _FakeResponse(json_body={"access_token": "m2m-token", "expires_in": 3600})
        method = (body or {}).get("method")
        if method == "initialize":
            state["initializes"] += 1
            return _FakeResponse(
                json_body={"jsonrpc": "2.0", "id": 1, "result": {}},
                headers={"mcp-session-id": "sess-1", "content-type": "application/json"},
            )
        if method == "notifications/initialized":
            return _FakeResponse(status_code=202)
        if method == "tools/call":
            state["tool_calls"] += 1
            if state["tool_calls"] == 1:
                return _FakeResponse(status_code=404)
            return _FakeResponse(
                json_body={"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "ok"}]}}
            )
        raise AssertionError(method)

    _fake_transport(monkeypatch, handler)

    result = await mcp_client.call_mcp_tool("echoTool", {}, "tenant_alpha", "00-a-b-01")

    assert mcp_client.tool_result_text(result) == "ok"
    assert state["initializes"] == 2


@pytest.mark.asyncio
async def test_sse_response_selects_the_result_envelope(monkeypatch):
    sse_body = (
        'event: message\n'
        'data: {"jsonrpc": "2.0", "method": "notifications/message", "params": {}}\n'
        '\n'
        'data: {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "from-sse"}]}}\n'
        '\n'
    )

    def handler(url, body, headers):
        if url.endswith("/oauth/token"):
            return _FakeResponse(json_body={"access_token": "m2m-token", "expires_in": 3600})
        method = (body or {}).get("method")
        if method == "initialize":
            return _FakeResponse(
                json_body={"jsonrpc": "2.0", "id": 1, "result": {}},
                headers={"mcp-session-id": "sess-1", "content-type": "application/json"},
            )
        if method == "notifications/initialized":
            return _FakeResponse(status_code=202)
        if method == "tools/call":
            return _FakeResponse(
                status_code=200,
                headers={"content-type": "text/event-stream"},
                text=sse_body,
            )
        raise AssertionError(method)

    _fake_transport(monkeypatch, handler)

    result = await mcp_client.call_mcp_tool("echoTool", {}, "tenant_alpha", "00-a-b-01")
    assert mcp_client.tool_result_text(result) == "from-sse"


@pytest.mark.asyncio
async def test_json_rpc_error_maps_to_tool_error(monkeypatch):
    def handler(url, body, headers):
        if url.endswith("/oauth/token"):
            return _FakeResponse(json_body={"access_token": "m2m-token", "expires_in": 3600})
        method = (body or {}).get("method")
        if method == "initialize":
            return _FakeResponse(
                json_body={"jsonrpc": "2.0", "id": 1, "result": {}},
                headers={"mcp-session-id": "sess-1", "content-type": "application/json"},
            )
        if method == "notifications/initialized":
            return _FakeResponse(status_code=202)
        if method == "tools/call":
            return _FakeResponse(
                json_body={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "error": {"code": -32602, "message": "invalid params"},
                }
            )
        raise AssertionError(method)

    _fake_transport(monkeypatch, handler)

    with pytest.raises(mcp_client.McpToolError) as exc:
        await mcp_client.call_mcp_tool("echoTool", {}, "tenant_alpha", "00-a-b-01")
    assert exc.value.code == -32602
    assert "invalid params" in exc.value.message
