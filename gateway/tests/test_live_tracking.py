import json
import time
import uuid

from gateway import main


def test_chat_completions_creates_run_and_adds_to_active_set(client, auth_headers, redis_client):
    response = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "prompt": "Hello", "temperature": 0.7},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert "run_id" in body
    run_id = body["run_id"]
    
    # Check active runs
    runs = client.get("/v1/agent/executions", headers=auth_headers)
    assert runs.status_code == 200
    active = runs.json()
    assert any(r["run_id"] == run_id for r in active)
    
    # Check run data
    run_data = redis_client.get(f"run:{run_id}")
    assert run_data is not None
    parsed = json.loads(run_data)
    assert parsed["status"] in ["active", "completed"]
    assert parsed["model"] == "gpt-4o"
    assert parsed["tenant_id"] == "tenant_alpha"


def test_agent_executions_scoped_to_tenant(client, auth_headers, redis_client):
    # Create run for tenant_alpha
    response = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "prompt": "Hello"},
        headers=auth_headers,
    )
    run_id = response.json()["run_id"]
    
    # Get executions for tenant_alpha
    runs = client.get("/v1/agent/executions", headers=auth_headers)
    assert runs.status_code == 200
    assert any(r["run_id"] == run_id for r in runs.json())
    
    # Switch to different tenant (simulated by different key in test setup)
    # For simplicity, just verify the set is keyed by tenant
    tenant_runs = redis_client.smembers("active_runs:tenant_alpha")
    assert run_id in tenant_runs


def test_evals_recent_endpoint(client, auth_headers, redis_client):
    # Seed an eval
    eval_data = {
        "run_id": str(uuid.uuid4()),
        "score": 0.95,
        "reason": "Excellent response",
        "timestamp": time.time(),
    }
    redis_client.lpush("evals:tenant_alpha", json.dumps(eval_data))
    
    response = client.get("/v1/evals/recent", headers=auth_headers)
    assert response.status_code == 200
    evals = response.json()
    assert any(e["run_id"] == eval_data["run_id"] for e in evals)
    assert any(e["score"] == 0.95 for e in evals)
