"""Auth0 token verification and tenant binding (spec 0004).

Covers the acceptance criteria in `docs/specs/0004-auth0-integration.md`:
- AC-1: unauthenticated or invalid requests to `/v1/*` are 401.
- AC-2: tokens are verified against the Auth0 JWKS endpoint.
- AC-3: `sub`, the namespaced tenant claim, and `permissions` reach request state.
- AC-4: the verified tenant replaces any `X-Tenant-ID` header.
- AC-5: authenticated telemetry inherits the verified tenant and user.

The tokens here are genuinely RSA signed and verified, not stubbed. `test_rbac.py`
covers the permission branch by passing claims straight into the dependency, which
is the right way to isolate RBAC but skips the whole signature path: nothing
between the `Authorization` header and the claim dict was under test. A verifier
that ignored the signature, the expiry, or the audience would have passed that
suite. So `_jwks_client` is replaced with a stub returning a locally generated
key, and everything downstream of it, including `jwt.decode`, is the real thing.

That matters most for the claim rename in `auth0/README.md`. A token carrying the
old `https://agentshield.com/tenant_id` claim is rejected here with 403, which is
the same dead end the README describes for a user whose Post-Login Action never
ran. It is a confusing failure by design (a valid session, a 403, no way to
recover) so it gets explicit coverage.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from config.settings import settings
from gateway.auth import (
    _bind_tenant_state,
    _jwks_client,
    get_verified_tenant,
    require_permission,
    verify_jwt,
)
from gateway.main import app


def settings_jwks_url() -> str:
    return f"https://{settings.auth0_domain}/.well-known/jwks.json"

TENANT_CLAIM = "https://api.agentshield.local/tenant_id"
LEGACY_TENANT_CLAIM = "https://agentshield.com/tenant_id"

AUDIENCE = "https://api.agentshield.local"
ISSUER = "https://dev-zymaiayb0afkpn7n.us.auth0.com/"

MISSING_CLAIM_DETAIL = "Token missing mandatory tenant identification claim"

client = TestClient(app)

# Routes that exist only to observe what a dependency bound, so these tests do
# not depend on any business route's own authorization rules.
probe_app = FastAPI()


@probe_app.get("/whoami")
async def whoami(claims: dict = Depends(verify_jwt)) -> dict:
    """Exposes the claims `verify_jwt` returned, before tenant binding."""
    return {"claims": claims}


@probe_app.get("/bound")
async def bound(request: Request, tenant_id: str = Depends(get_verified_tenant)) -> dict:
    """Exposes the state `get_verified_tenant` bound, and the caller's headers."""
    return {
        "tenant_id": tenant_id,
        "state_tenant": request.state.tenant_id,
        "state_user": request.state.user_id,
        "state_verified": request.state.principal_verified,
        "permissions": sorted(request.state.permissions),
        "header_tenant": request.headers.get("X-Tenant-ID"),
    }


@probe_app.get("/scoped")
async def scoped(_ok: bool = Depends(require_permission("keys:write"))) -> dict:
    return {"allowed": True}


probe_client = TestClient(probe_app)


@pytest.fixture(scope="module")
def signing_key():
    """A real RSA key. Generated once; a fresh one per test is needless cost."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def _no_dev_mode(monkeypatch):
    """Never let the DEV_MODE stand-in answer a test about real verification.

    `verify_token_credentials` short-circuits on two literal token strings when
    DEV_MODE is true, so an unset variable would quietly return fixture claims
    for any input and the whole file would pass without verifying anything.
    """
    monkeypatch.delenv("DEV_MODE", raising=False)


@pytest.fixture
def jwks(signing_key):
    """Serve the generated public key wherever the verifier looks for JWKS.

    Replaces the client object, not `jwt.decode`, so signature, expiry, audience,
    and issuer validation all still run for real.
    """

    class StubJWKSClient:
        def get_signing_key_from_jwt(self, token):
            return type("Key", (), {"key": signing_key.public_key()})()

    with patch("gateway.auth._jwks_client", return_value=StubJWKSClient()):
        yield


def make_token(signing_key, **overrides) -> str:
    """Build a token Auth0 would issue, with any claim overridable.

    Defaults describe a working request for tenant_alpha. Overriding `exp`,
    `aud`, or `iss` produces the specific token that must be refused.
    """
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "auth0|user_alpha_1",
        TENANT_CLAIM: "tenant_alpha",
        "permissions": ["tools:execute", "keys:write"],
        "aud": AUDIENCE,
        "iss": ISSUER,
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    for key, value in overrides.items():
        if value is _REMOVED:
            claims.pop(key, None)
        else:
            claims[key] = value
    return jwt.encode(claims, signing_key, algorithm="RS256")


class _Removed:
    """Sentinel distinguishing 'set this claim to None' from 'omit it'."""

    def __repr__(self):  # pragma: no cover - debugging aid
        return "<removed>"


_REMOVED = _Removed()
REMOVE = _REMOVED


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestValidTokenIsAccepted:
    """AC-2, AC-3: a genuine signature is verified and its claims survive."""

    def test_valid_token_returns_its_claims(self, signing_key, jwks):
        token = make_token(signing_key)

        response = probe_client.get("/whoami", headers=auth(token))

        assert response.status_code == 200, response.text
        assert response.json()["claims"][TENANT_CLAIM] == "tenant_alpha"

    def test_claims_bind_to_request_state(self, signing_key, jwks):
        """AC-3: sub, tenant, and permissions all land where the app reads them."""
        token = make_token(signing_key, permissions=["tools:execute", "keys:write"])

        body = probe_client.get("/bound", headers=auth(token)).json()

        assert body["tenant_id"] == "tenant_alpha"
        assert body["state_tenant"] == "tenant_alpha"
        assert body["state_user"] == "auth0|user_alpha_1"
        assert body["state_verified"] is True
        assert body["permissions"] == ["keys:write", "tools:execute"]

    def test_a_different_tenant_resolves_to_its_own_tenant(self, signing_key, jwks):
        """The tenant is read from the claim, so it is not a fixed constant."""
        token = make_token(signing_key, **{TENANT_CLAIM: "tenant_beta"})

        body = probe_client.get("/bound", headers=auth(token)).json()

        assert body["tenant_id"] == "tenant_beta"
        assert body["state_tenant"] == "tenant_beta"


class TestInvalidTokensAreRefused:
    """AC-1: everything that is not a valid token for this API is a 401."""

    def test_no_authorization_header_is_401(self):
        response = probe_client.get("/whoami")

        assert response.status_code == 401
        assert response.json()["detail"] == "Not authenticated"

    @pytest.mark.parametrize("scheme", ["Basic dXNlcjpwYXNz", "Token abc123", "Bearer"])
    def test_a_non_bearer_scheme_is_401(self, scheme):
        response = probe_client.get("/whoami", headers={"Authorization": scheme})

        assert response.status_code == 401

    def test_an_empty_bearer_token_is_401(self):
        response = probe_client.get("/whoami", headers={"Authorization": "Bearer "})

        assert response.status_code == 401

    def test_a_token_signed_by_another_key_is_401(self, signing_key, jwks):
        """AC-2: the signature is actually checked, not merely decoded.

        The decisive test for the whole file. A verifier that skipped signature
        validation would accept this and every happy path would still pass.
        """
        attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged = jwt.encode(
            {
                "sub": "auth0|attacker",
                TENANT_CLAIM: "tenant_victim",
                "permissions": ["keys:write"],
                "aud": AUDIENCE,
                "iss": ISSUER,
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            },
            attacker_key,
            algorithm="RS256",
        )

        response = probe_client.get("/whoami", headers=auth(forged))

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid access token"

    def test_an_unsigned_none_algorithm_token_is_401(self, jwks):
        """The `alg: none` downgrade must not be honoured.

        A naive verifier that passes `verify_signature=False`, or trusts the
        token's own `alg` header, would accept an unsigned token claiming to be
        a tenant admin. The verifier pins RS256 (`gateway/auth.py:62`).
        """
        unsigned = jwt.encode(
            {
                "sub": "auth0|attacker",
                TENANT_CLAIM: "tenant_victim",
                "permissions": ["keys:write"],
                "aud": AUDIENCE,
                "iss": ISSUER,
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            },
            key="",
            algorithm="none",
        )

        response = probe_client.get("/whoami", headers=auth(unsigned))

        assert response.status_code == 401

    def test_a_garbage_token_is_401(self, jwks):
        response = probe_client.get("/whoami", headers=auth("not.a.jwt"))

        assert response.status_code == 401

    def test_an_expired_token_is_401(self, signing_key, jwks):
        """AC-1. Expiry is the case a 24h session eventually hits in production."""
        token = make_token(
            signing_key, exp=datetime.now(timezone.utc) - timedelta(minutes=1)
        )

        response = probe_client.get("/whoami", headers=auth(token))

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid access token"

    def test_a_token_for_another_audience_is_401(self, signing_key, jwks):
        """A valid Auth0 token minted for a different API must not work here.

        Same issuer, same signature, valid signature: only the audience differs.
        Without the audience check, a token for any other API in the tenant would
        authenticate against the gateway.
        """
        token = make_token(signing_key, aud="https://some-other-api.example.com")

        response = probe_client.get("/whoami", headers=auth(token))

        assert response.status_code == 401

    def test_a_token_from_another_issuer_is_401(self, signing_key, jwks):
        """Guards against a token from a different Auth0 tenant being replayed."""
        token = make_token(signing_key, iss="https://evil-tenant.us.auth0.com/")

        response = probe_client.get("/whoami", headers=auth(token))

        assert response.status_code == 401


class TestMissingTenantClaimIsForbidden:
    """The 403 branch, which is the failure the Auth0 README warns about.

    A token that verifies but carries no tenant claim is a 403, not a 401: the
    caller is who they say they are, they just cannot be placed in a tenant. The
    distinction is load-bearing, because spec 0013 requires the portal never to
    treat a 403 as an expired session. The user is stuck until an admin seeds
    `app_metadata` and the Post-Login Action stamps the claim.
    """

    def test_a_token_without_the_tenant_claim_is_403(self, signing_key, jwks):
        token = make_token(signing_key, **{TENANT_CLAIM: REMOVE})

        response = probe_client.get("/bound", headers=auth(token))

        assert response.status_code == 403
        assert response.json()["detail"] == MISSING_CLAIM_DETAIL

    def test_an_empty_tenant_claim_is_403(self, signing_key, jwks):
        """Falsy is refused, not just absent.

        `_bind_tenant_state` would otherwise stringify the empty value into
        `request.state.tenant_id`, binding the request to a tenant named `""`
        rather than refusing it.
        """
        token = make_token(signing_key, **{TENANT_CLAIM: ""})

        response = probe_client.get("/bound", headers=auth(token))

        assert response.status_code == 403
        assert response.json()["detail"] == MISSING_CLAIM_DETAIL

    def test_the_legacy_marketing_domain_claim_is_403(self, signing_key, jwks):
        """The pre-rename claim name no longer places a caller in a tenant.

        `auth0/README.md` records that the claim moved off
        `https://agentshield.com/tenant_id`. A token minted before the rename
        carries the old name and is refused here. Pinned deliberately: the rename
        was behaviour-preserving only because the reader looks up exactly one
        namespaced key, and this is the test that fails if it ever looks at two.
        """
        token = make_token(
            signing_key,
            **{
                TENANT_CLAIM: REMOVE,
                LEGACY_TENANT_CLAIM: "tenant_alpha",
            },
        )

        response = probe_client.get("/bound", headers=auth(token))

        assert response.status_code == 403
        assert response.json()["detail"] == MISSING_CLAIM_DETAIL

    def test_a_missing_claim_does_not_403_when_the_token_is_also_invalid(self, signing_key, jwks):
        """Order matters: a bad signature is a 401 even with no tenant claim.

        Otherwise a forged token could probe for the claim's presence by reading
        the status code, and an invalid token would be reported with a detail
        that suggests fixing `app_metadata`, sending the operator after the wrong
        problem entirely.
        """
        attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged = jwt.encode(
            {"sub": "auth0|attacker", "aud": AUDIENCE, "iss": ISSUER},
            attacker_key,
            algorithm="RS256",
        )

        response = probe_client.get("/bound", headers=auth(forged))

        assert response.status_code == 401


class TestHeaderCannotOverrideTheVerifiedTenant:
    """AC-4: the claim wins over anything the caller sends."""

    def test_x_tenant_id_header_cannot_change_the_tenant(self, signing_key, jwks):
        token = make_token(signing_key)

        body = probe_client.get(
            "/bound", headers={**auth(token), "X-Tenant-ID": "tenant_beta"}
        ).json()

        assert body["tenant_id"] == "tenant_alpha"
        assert body["state_tenant"] == "tenant_alpha"
        # The header is still readable, which is why no handler may trust it.
        assert body["header_tenant"] == "tenant_beta"

    def test_tenant_comes_from_the_claim_even_when_the_header_agrees(self, signing_key, jwks):
        """A matching header proves nothing, so the claim is still the source."""
        token = make_token(signing_key)

        body = probe_client.get(
            "/bound", headers={**auth(token), "X-Tenant-ID": "tenant_alpha"}
        ).json()

        assert body["state_tenant"] == "tenant_alpha"


class TestPermissionClaimHandling:
    """`permissions` is attacker-shaped input and is normalised defensively.

    `_bind_tenant_state` does `set(permissions) if isinstance(permissions, list)`
    (`gateway/auth.py:98`). A non-list therefore has to fail closed to an empty
    set rather than become a set of characters, which is what the Auth0 Action's
    own guard is protecting against on the issuing side.
    """

    def test_a_list_of_permissions_becomes_a_set(self, signing_key, jwks):
        token = make_token(signing_key, permissions=["tools:execute", "keys:write"])

        body = probe_client.get("/bound", headers=auth(token)).json()

        assert body["permissions"] == ["keys:write", "tools:execute"]

    def test_an_absent_permissions_claim_yields_no_scopes(self, signing_key, jwks):
        """Missing is not the same as empty-granted: the caller holds nothing.

        RBAC then fails closed with a 403 naming the missing scope, which is the
        intended outcome for an incompletely seeded user.
        """
        token = make_token(signing_key, permissions=REMOVE)

        response = probe_client.get("/scoped", headers=auth(token))

        assert response.status_code == 403
        assert "keys:write" in response.json()["detail"]

    def test_a_string_permissions_claim_yields_no_scopes(self, signing_key, jwks):
        """A string must not become {'k','e','y',':'...} and then match a scope.

        Asserts the *bound set*, not just the 403. Checking only the status code
        is what let a real regression through: `set("keys:write")` is a set of
        nine single characters, so `"keys:write" not in permissions` holds by
        coincidence and the request is refused even when the type check is gone.
        The empty set is the actual contract, and only asserting it catches the
        bug. The failure this prevents is a one-character scope, or any future
        check that treats the value as a substring.
        """
        token = make_token(signing_key, permissions="keys:write")

        body = probe_client.get("/bound", headers=auth(token)).json()

        assert body["permissions"] == [], "a string claim must normalise to no scopes"

        response = probe_client.get("/scoped", headers=auth(token))
        assert response.status_code == 403

    def test_a_dict_permissions_claim_yields_no_scopes(self, signing_key, jwks):
        token = make_token(signing_key, permissions={"keys:write": True})

        body = probe_client.get("/bound", headers=auth(token)).json()

        assert body["permissions"] == []
        assert probe_client.get("/scoped", headers=auth(token)).status_code == 403

    def test_a_numeric_permissions_claim_yields_no_scopes(self, signing_key, jwks):
        """Not iterable, so it would raise rather than degrade.

        `_bind_tenant_state` runs inside the dependency, so an unguarded
        `set(...)` over an int is a 500 on every authenticated request rather
        than a refused one. Normalising to an empty set fails closed instead.
        """
        token = make_token(signing_key, permissions=42)

        response = probe_client.get("/bound", headers=auth(token))

        assert response.status_code == 200
        assert response.json()["permissions"] == []

    def test_permissions_are_matched_exactly(self, signing_key, jwks):
        """A prefix or superset is not a match; the string is a contract.

        `auth0/README.md` calls the spelling load-bearing. `keys:write:all` and
        `keys` must not satisfy a route requiring `keys:write`.
        """
        for permissions in (["keys"], ["keys:write:all"], ["KEYS:WRITE"], ["write"]):
            token = make_token(signing_key, permissions=permissions)
            response = probe_client.get("/scoped", headers=auth(token))
            assert response.status_code == 403, f"{permissions} should not grant keys:write"


class TestGatewayRoutesUseTheSameVerification:
    """The real app, not the probe app, so wiring mistakes show up here too."""

    def test_a_valid_token_reaches_a_protected_route(self, signing_key, jwks):
        token = make_token(signing_key, sub="auth0|user_alpha_1")

        response = client.get("/api/v1/protected", headers=auth(token))

        assert response.status_code == 200, response.text
        assert response.json()["user"] == "auth0|user_alpha_1"

    def test_a_protected_route_refuses_a_forged_token(self, signing_key, jwks):
        attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged = jwt.encode(
            {
                "sub": "auth0|attacker",
                TENANT_CLAIM: "tenant_victim",
                "aud": AUDIENCE,
                "iss": ISSUER,
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            },
            attacker_key,
            algorithm="RS256",
        )

        response = client.get("/api/v1/protected", headers=auth(forged))

        assert response.status_code == 401

    def test_a_protected_route_refuses_a_token_with_no_tenant_claim(self, signing_key, jwks):
        token = make_token(signing_key, **{TENANT_CLAIM: REMOVE})

        response = client.get("/api/v1/protected", headers=auth(token))

        assert response.status_code == 403
        assert response.json()["detail"] == MISSING_CLAIM_DETAIL


class TestJwksClientIsCached:
    """AC-2, Option 1: keys are fetched once, not per request.

    The whole point of the cached `PyJWKClient` is that verification does no
    network call. `_jwks_client` is `lru_cache`d, so this asserts the cache is
    wired, not that a fetch happened once.
    """

    def test_the_client_is_built_once(self):
        _jwks_client.cache_clear()
        try:
            first = _jwks_client()
            second = _jwks_client()

            assert first is second, "the JWKS client must be cached across calls"
            assert settings_jwks_url() in str(first.uri)
        finally:
            _jwks_client.cache_clear()


class TestBindTenantStateDirectly:
    """Unit coverage for the binding helper, including states the routes avoid.

    `_bind_tenant_state` is what every authenticated request funnels through, and
    it coerces rather than validates, so these pin the coercion itself instead of
    reaching it through a route.
    """

    def test_it_sets_every_field_the_telemetry_path_reads(self):
        """AC-5: telemetry reads tenant_id and user_id from this state."""
        from starlette.requests import Request as StarletteRequest

        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        request = StarletteRequest(scope)

        tenant_id = _bind_tenant_state(
            request,
            {"sub": "auth0|user_1", TENANT_CLAIM: "tenant_gamma", "permissions": ["logs:read"]},
        )

        assert tenant_id == "tenant_gamma"
        assert request.state.tenant_id == "tenant_gamma"
        assert request.state.user_id == "auth0|user_1"
        assert request.state.principal_verified is True
        assert request.state.permissions == {"logs:read"}

    def test_a_non_string_tenant_claim_is_coerced_not_dropped(self):
        """An int tenant becomes its string form rather than vanishing.

        Auth0 issues strings, so this cannot happen in production. It is pinned
        because the helper coerces with `str()` and a reader that assumed a
        string would compare `1200 != "1200"` and read a tenant as missing.
        """
        from starlette.requests import Request as StarletteRequest

        request = StarletteRequest({"type": "http", "method": "GET", "path": "/", "headers": []})

        tenant_id = _bind_tenant_state(request, {"sub": "u", TENANT_CLAIM: 1200})

        assert tenant_id == "1200"
        assert request.state.tenant_id == "1200"
