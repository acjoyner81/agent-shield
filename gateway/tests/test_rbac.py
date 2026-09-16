import pytest
import os
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from gateway.main import app

client = TestClient(app)

@pytest.fixture(autouse=True)
def enable_dev_mode():
    os.environ["DEV_MODE"] = "true"
    yield
    if "DEV_MODE" in os.environ:
        del os.environ["DEV_MODE"]

def test_rbac_execute_tool_success():
    """AC-1, AC-2: User with tools:execute scope succeeds on /v1/tools/execute."""
    headers = {
        "Authorization": "Bearer dev-mock-token"
    }
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = lambda: {"result": "success_output"}
        mock_response.raise_for_status = lambda: None
        mock_post.return_value = mock_response
        
        response = client.post(
            "/v1/tools/execute",
            headers=headers,
            json={"tool_name": "test_tool", "params": {}}
        )
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["result"] == "success_output"

def test_rbac_execute_tool_forbidden():
    """AC-3, AC-4: User missing required scope gets HTTP 403 Forbidden with detail message."""
    headers = {
        "Authorization": "Bearer dev-unprivileged-token"
    }
    response = client.post(
        "/v1/tools/execute",
        headers=headers,
        json={"tool_name": "test_tool", "params": {}}
    )
    
    assert response.status_code == 403
    assert response.json()["detail"] == "Permission denied: missing required scope 'tools:execute'"
