from fastapi.testclient import TestClient


def test_openapi_and_docs_routes():
    from gateway.main import app

    client = TestClient(app, base_url="http://localhost")
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert "openapi" in data
    assert isinstance(data.get("paths"), dict)

    resp = client.get("/api/openapi.json")
    assert resp.status_code == 200
    assert "openapi" in resp.json()

    resp = client.get("/docs")
    assert resp.status_code == 200
    body = resp.text
    assert "swagger" in body.lower()
    assert "/api/openapi.json" in body
