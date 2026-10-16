"""Client for the mcp-server streamable HTTP transport (spec 0015.2, M-AC-8).

The gateway authenticates to mcp-server with its own Auth0 client-credentials
token, never the caller's JWT. The token is cached with an expiry skew and a
single 401 retry covers a rotation or expiry race; a stale MCP session is
re-initialized once on a 404. The calling tenant travels as a label header that
mcp-server uses for logging only, and it never authorizes anything.

The transport is spoken directly over httpx (no MCP SDK dependency), matching
the rest of the gateway's HTTP client usage and keeping the call seam mockable
in tests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import httpx

from config.settings import settings

logger = logging.getLogger("agentshield.gateway.mcp")

MCP_ENDPOINT = os.getenv("MCP_SERVER_URL") or settings.mcp_server_url
MCP_PROTOCOL_VERSION = "2025-06-18"
TOKEN_EXPIRY_SKEW_SECONDS = 60
CLIENT_INFO = {"name": "agentshield-gateway", "version": "1.0.0"}


class McpConfigurationError(RuntimeError):
    """The machine-to-machine credentials the call needs are not configured."""


class McpToolError(RuntimeError):
    """A JSON-RPC error object returned by mcp-server for a tools/call."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_token_cache: dict[str, object] = {"value": None, "expires_at": 0.0}
_token_lock = asyncio.Lock()
_session_id: str | None = None


def reset_caches() -> None:
    """Drop the cached token and MCP session. Tests call this between cases."""
    global _session_id, _token_lock
    _token_cache["value"] = None
    _token_cache["expires_at"] = 0.0
    _session_id = None
    # Rebuild the lock so it binds to the caller's event loop rather than a
    # loop from a previous test/request cycle.
    _token_lock = asyncio.Lock()


async def get_access_token(force_refresh: bool = False) -> str:
    """Return a cached client-credentials token, minting a fresh one if needed.

    The cache is keyed off a monotonic clock so a wall-clock jump cannot pin a
    stale token. A refresh is serialized so a burst of tool calls mints once.
    """
    now = time.monotonic()
    cached = _token_cache["value"]
    if not force_refresh and cached and now < _token_cache["expires_at"]:
        return str(cached)

    client_id = settings.auth0_m2m_client_id
    client_secret = settings.auth0_m2m_client_secret
    if not client_id or not client_secret:
        raise McpConfigurationError(
            "AUTH0_M2M_CLIENT_ID and AUTH0_M2M_CLIENT_SECRET must be set for the "
            "gateway to call mcp-server."
        )

    async with _token_lock:
        # Another coroutine may have refreshed while we waited for the lock.
        cached = _token_cache["value"]
        if not force_refresh and cached and time.monotonic() < _token_cache["expires_at"]:
            return str(cached)

        token_url = f"https://{settings.auth0_domain}/oauth/token"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                token_url,
                json={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "audience": settings.auth0_audience,
                },
            )
            response.raise_for_status()
            payload = response.json()

        access_token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 3600))
        _token_cache["value"] = access_token
        _token_cache["expires_at"] = time.monotonic() + max(
            expires_in - TOKEN_EXPIRY_SKEW_SECONDS, 0
        )
        return str(access_token)


def _headers(token: str, session_id: str | None, extra: dict[str, str]) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        **extra,
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def _initialize_request() -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        },
    }


def _tool_request(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }


def _parse_response(response: httpx.Response) -> dict[str, object]:
    """Decode a JSON-RPC response body that may arrive as JSON or an SSE event.

    A streamable response can carry more than one SSE event (notifications then
    the reply), so the response envelope is selected by having a `result` or
    `error`, not simply by being first.
    """
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        return response.json()

    fallback: dict[str, object] | None = None
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            message = json.loads(line[len("data:"):].strip())
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        if "result" in message or "error" in message:
            return message
        fallback = fallback or message
    if fallback is not None:
        return fallback
    raise McpToolError(-32603, "empty event stream from mcp-server")


async def _open_session(
    client: httpx.AsyncClient, token: str, extra: dict[str, str]
) -> str | None:
    """Run the initialize handshake and remember the session id it returns."""
    global _session_id
    init_response = await client.post(
        MCP_ENDPOINT, headers=_headers(token, None, extra), json=_initialize_request()
    )
    init_response.raise_for_status()
    session_id = init_response.headers.get("mcp-session-id")
    ack = await client.post(
        MCP_ENDPOINT,
        headers=_headers(token, session_id, extra),
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    ack.raise_for_status()
    _session_id = session_id
    return session_id


async def call_mcp_tool(
    tool_name: str,
    arguments: dict[str, object],
    tenant_id: str,
    traceparent: str,
) -> dict[str, object]:
    """Call a tool over the streamable transport and return its JSON-RPC result.

    Retries exactly once: a 401 refreshes the token, a 404 (dropped session)
    re-initializes. The caller's JWT is never sent; only this machine token and
    a logging-only tenant label are.
    """
    global _session_id
    extra = {"X-Tenant-ID": tenant_id, "traceparent": traceparent}
    token = await get_access_token()

    async with httpx.AsyncClient(timeout=30.0) as client:
        response: httpx.Response | None = None
        for attempt in (1, 2):
            try:
                if _session_id is None:
                    await _open_session(client, token, extra)
                response = await client.post(
                    MCP_ENDPOINT,
                    headers=_headers(token, _session_id, extra),
                    json=_tool_request(tool_name, arguments),
                )
                if response.status_code == 404 and attempt == 1:
                    _session_id = None
                    continue
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if attempt == 1 and status in (401, 404):
                    _session_id = None
                    if status == 401:
                        token = await get_access_token(force_refresh=True)
                    continue
                raise

        if response is None:
            raise McpToolError(-32603, "mcp-server returned no response")
        body = _parse_response(response)

    error = body.get("error")
    if isinstance(error, dict):
        raise McpToolError(
            int(error.get("code", -32603)), str(error.get("message", "tool call failed"))
        )
    result = body.get("result")
    return result if isinstance(result, dict) else {}


def tool_result_text(result: dict[str, object]) -> str:
    """Concatenate the text blocks of an MCP tools/call result.

    The MCP contract returns a content array; the gateway's callers historically
    read a single `result` value, so the text blocks are joined to preserve that
    shape without leaking protocol detail to the API surface.
    """
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    parts = [
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(parts)
