import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from gateway.main import app, verify_jwt

client = TestClient(app)


def test_rbac_execute_tool_success():
    """AC-1, AC-2: User with tools:execute scope succeeds on /v1/tools/execute."""
    async def mock_verify_jwt_success():
        return {
            "sub": "user_123",
            "permissions": ["tools:execute"],
            "scope": "tools:execute"
        }

    app.dependency_overrides[verify_jwt] = mock_verify_jwt_success

    # Build a mock response object
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": "success_output"}
    mock_response.raise_for_status = lambda: None

    # Configure AsyncClient instance with async post method
    mock_client_instance = MagicMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)

    # Configure async context manager (__aenter__ / __aexit__)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=None)

    try:
        with patch("gateway.main.httpx.AsyncClient", return_value=mock_client_instance):
            response = client.post(
                "/v1/tools/execute",
                headers={"Authorization": "Bearer dev-mock-token"},
                json={"tool_name": "test_tool", "params": {}}
            )
        assert response.status_code in [200, 201]
    finally:
        app.dependency_overrides.clear()


def test_rbac_execute_tool_forbidden():
    """AC-3: User without tools:execute scope receives 403 Forbidden."""
    async def mock_verify_jwt_unprivileged():
        return {
            "sub": "user_456",
            "permissions": ["read:logs"],
            "scope": "read:logs"
        }

    app.dependency_overrides[verify_jwt] = mock_verify_jwt_unprivileged

    try:
        response = client.post(
            "/v1/tools/execute",
            headers={"Authorization": "Bearer dev-unprivileged-token"},
            json={"tool_name": "test_tool", "params": {}}
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()