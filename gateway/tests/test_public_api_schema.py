"""Public API contract tests (Spec 0012).

Locks the published OpenAPI surface so the documentation cannot drift from the
runtime: the four contract routes are present, operational routes are absent,
both auth modes are declared with their precedence and tenant rules, and no
example leaks a credential.
"""

import json
import re

import pytest
from fastapi.testclient import TestClient

from gateway.main import app

client = TestClient(app)

CONTRACT_PATHS = {
    "/v1/chat/completions",
    "/v1/tools/execute",
    "/v1/usage/summary",
    "/v1/keys",
    "/v1/keys/{key_id}",
    "/v1/keys/{key_id}/rotate",
}

# AC-5: operational, internal, and portal alias surfaces.
EXCLUDED_PATHS = {
    "/health",
    "/v1/health/services",
    "/api/v1/health/services",
    "/v1/telemetry/logs",
    "/api/v1/telemetry/logs",
    "/v1/billing/webhook",
    "/api/v1/billing/webhook",
    "/v1/webhooks/stripe",
    "/api/v1/protected",
    "/v1/billing/checkout",
    "/v1/billing/portal",
}


@pytest.fixture(scope="module")
def schema() -> dict:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()


def operations(paths: dict) -> dict:
    """Flatten a paths object into `{path: {method: operation}}`."""
    return {
        path: {m: op for m, op in item.items() if isinstance(op, dict)}
        for path, item in paths.items()
    }


# ---------------------------------------------------------------------------
# AC-1: the schema is public and describes the contract routes
# ---------------------------------------------------------------------------

def test_openapi_json_is_public(schema):
    assert "openapi" in schema
    assert schema["info"]["title"] == "AgentShield Enterprise API Gateway"


def test_docs_page_is_public():
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower()


def test_contract_routes_present(schema):
    """AC-1: the four machine contract routes are described."""
    assert CONTRACT_PATHS <= set(schema["paths"])


def test_every_contract_operation_has_summary_and_tags(schema):
    """AC-1: per operation summaries and tags."""
    for path, methods in operations(schema["paths"]).items():
        if path not in CONTRACT_PATHS:
            continue
        for method, op in methods.items():
            assert op.get("summary"), f"{method.upper()} {path} has no summary"
            assert op.get("tags"), f"{method.upper()} {path} has no tags"
            assert op.get("description"), f"{method.upper()} {path} has no description"


def test_every_contract_operation_has_an_example(schema):
    """AC-1: request or response examples on each contract operation.

    A bodyless operation (204 revoke) has no payload to illustrate, so it must
    instead document the outcomes a caller can hit.
    """
    for path, methods in operations(schema["paths"]).items():
        if path not in CONTRACT_PATHS:
            continue
        for method, op in methods.items():
            responses = op.get("responses", {})
            media = list((op.get("requestBody") or {}).get("content", {}).values())
            for response in responses.values():
                if isinstance(response, dict):
                    media.extend(response.get("content", {}).values())

            if any(c.get("examples") or c.get("example") for c in media):
                continue

            # A bodyless success (204 revoke) has no payload to illustrate. FastAPI
            # still synthesises a 422 validation body, so judge on the 2xx only.
            success = [
                r for code, r in responses.items()
                if code.startswith("2") and isinstance(r, dict)
            ]
            bodyless = not op.get("requestBody") and all(
                not r.get("content") for r in success
            )
            assert bodyless, f"{method.upper()} {path} has no request or response example"

            # A 204-only operation still has to say what can go wrong.
            documented = {c for c in responses if not c.startswith("2") and c != "default"}
            assert documented, f"{method.upper()} {path} documents no outcome"


def test_operational_routes_excluded(schema):
    """AC-5: health, telemetry ingest, webhook, and billing UI stay out."""
    assert EXCLUDED_PATHS.isdisjoint(set(schema["paths"]))


def test_portal_aliases_excluded(schema):
    """AC-5: the `/api` portal aliases are not part of the public contract."""
    assert not [p for p in schema["paths"] if p.startswith("/api/")]


def test_published_paths_are_exactly_the_contract(schema):
    """AC-1/AC-5: the published inventory is the contract and nothing else."""
    assert set(schema["paths"]) == CONTRACT_PATHS


# ---------------------------------------------------------------------------
# AC-2: both authentication modes and their precedence
# ---------------------------------------------------------------------------

def test_security_schemes_defined(schema):
    """AC-2: Bearer JWT and the X-Tenant-API-Key header are both declared."""
    schemes = schema["components"]["securitySchemes"]
    assert schemes["bearer"]["scheme"] == "bearer"
    assert schemes["apiKey"]["type"] == "apiKey"
    assert schemes["apiKey"]["in"] == "header"
    assert schemes["apiKey"]["name"] == "X-Tenant-API-Key"


def test_contract_operations_require_a_credential(schema):
    """AC-2: every contract operation offers both auth modes."""
    for path, methods in operations(schema["paths"]).items():
        for method, op in methods.items():
            security = op.get("security", [])
            assert {"bearer": []} in security, f"{method.upper()} {path} lacks Bearer"
            assert {"apiKey": []} in security, f"{method.upper()} {path} lacks apiKey"


def test_precedence_note_present(schema):
    """AC-2: the schema states that the Bearer token wins."""
    description = schema["components"]["securitySchemes"]["bearer"]["description"]
    assert "precedence" in description.lower()
    assert "X-Tenant-API-Key" in description


# ---------------------------------------------------------------------------
# AC-3: a caller supplied X-Tenant-ID is never authoritative
# ---------------------------------------------------------------------------

def test_spoofed_tenant_header_is_documented(schema):
    """AC-3: the X-Tenant-ID rule is stated in the published schema."""
    description = schema["info"]["description"]
    assert "X-Tenant-ID" in description
    assert "never authoritative" in description.lower()

    for scheme in ("bearer", "apiKey"):
        text = schema["components"]["securitySchemes"][scheme]["description"]
        assert "X-Tenant-ID" in text
        assert "never authoritative" in text.lower()


def test_tenant_id_header_is_not_an_accepted_parameter(schema):
    """AC-3: the spoofable header is not offered as a request parameter."""
    for path, methods in operations(schema["paths"]).items():
        for method, op in methods.items():
            names = {
                p.get("name") for p in op.get("parameters", []) if isinstance(p, dict)
            }
            assert "X-Tenant-ID" not in names, f"{method.upper()} {path} accepts X-Tenant-ID"


# ---------------------------------------------------------------------------
# AC-5: no credential appears in any example
# ---------------------------------------------------------------------------

SECRET_SHAPES = [
    re.compile(r"sk_live_[A-Za-z0-9]{8,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}"),
    re.compile(r"whsec_[A-Za-z0-9]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
]


def test_no_secrets_in_examples(schema):
    """AC-5: examples are illustrative and never real credentials."""
    blob = json.dumps(schema)
    for pattern in SECRET_SHAPES:
        assert not pattern.search(blob), f"example leaks a secret matching {pattern.pattern}"


def test_key_example_does_not_return_a_live_secret(schema):
    """AC-5: the key example shows a masked prefix, not a usable key.

    Pydantic omits `None` fields from an example, so the secret is absent rather
    than null. Either way no usable secret is published.
    """
    example = schema["components"]["schemas"]["APIKeyResponse"]["examples"][0]["value"]
    assert example["key_prefix"] == "<masked>"
    assert not example.get("secret_key")
