def test_openapi_json_is_accessible(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    assert "openapi" in data
    assert data["info"]["title"] == "AgentShield Enterprise API Gateway"


def test_docs_endpoint_is_accessible(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


def test_servers_are_absolute(client):
    response = client.get("/openapi.json")
    data = response.json()
    servers = data.get("servers", [])
    # Check that at least one server has an absolute URL
    absolute_servers = [s for s in servers if s.get("url", "").startswith("http")]
    assert len(absolute_servers) >= 1
