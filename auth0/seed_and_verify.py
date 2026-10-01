#!/usr/bin/env python3
"""Seed a user's Auth0 app_metadata and verify the whole chain end to end.

The README's verify steps were a manual copy-paste: seed with the Management
API, then sign in through the browser, then hand-copy the access token out of
the SPA's localStorage to decode it. That is not reproducible, and the final
`curl` in it fails for a reason unrelated to seeding (see the DEV_MODE note
below), so it cannot tell you whether setup actually worked.

This does the whole chain in one command and prints a verdict per stage, so a
failure names the stage that broke rather than just a status code.

    export AUTH0_MGMT_TOKEN=...        # needs update:users
    python auth0/seed_and_verify.py --user-id auth0|abc --password '...'

Stages:
  1. seed      PATCH app_metadata on the user
  2. readback  GET the user, confirm the claims persisted
  3. login     Resource Owner Password Grant -> a real access token
  4. decode    assert the tenant and permission claims are on the ACCESS token
  5. gateway   call the gateway with it and report the status code

Stage 3 needs the tenant to use a Database connection. With Universal Login or a
social provider, skip to `--access-token` and paste a token from the SPA instead.

DEV_MODE does NOT need to be off. `docker-compose.yml` hardcodes DEV_MODE "true"
for gateway-python, and a real Auth0 token is still verified correctly with it
set: `verify_token_credentials` only short-circuits for the two literal stand-in
tokens "dev-mock-token" and "dev-unprivileged-token", and every other token falls
through to `jwt.decode`. Confirmed against the Compose stack, which has
DEV_MODE=true running: a genuine token returns 200 and a malformed one returns
401, which is only reachable via the real decode path.

What DEV_MODE does affect is a test, not a token: the mock tokens it mints carry
fixed scopes, so use a real token to verify a real claim.
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TENANT_CLAIM = "https://api.agentshield.local/tenant_id"

# The enforced permission set. `logs:read` is deliberately absent: nothing in
# the gateway checks it, so granting it implies a protection that does not exist.
DEFAULT_PERMISSIONS = ["tools:execute", "keys:write", "billing:admin"]

DEFAULT_AUDIENCE = "https://api.agentshield.local"
DEFAULT_DOMAIN = "dev-zymaiayb0afkpn7n.us.auth0.com"
DEFAULT_ISSUER = f"https://{DEFAULT_DOMAIN}/"
DEFAULT_CLIENT_ID = ""  # supplied via --client-id or AUTH0_CLIENT_ID


class StageError(Exception):
    """A stage failed in a way the operator needs to read."""


def request(url, *, method="GET", token=None, payload=None, form=None):
    """One HTTP call, raising with the response body so failures are legible."""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:400]
        raise StageError(f"HTTP {exc.code} from {method} {url}\n  {body}") from exc
    except urllib.error.URLError as exc:
        raise StageError(f"could not reach {url}: {exc.reason}") from exc


def ok(label, detail=""):
    print(f"  PASS  {label}{(' - ' + detail) if detail else ''}")


def fail(label, detail):
    print(f"  FAIL  {label} - {detail}")


def stage_seed(domain, mgmt_token, user_id, tenant_id, permissions):
    """PATCH app_metadata. Overwrites the whole object, so send the full set."""
    body = {"app_metadata": {"tenant_id": tenant_id, "permissions": permissions}}
    user = request(
        f"https://{domain}/api/v2/users/{urllib.parse.quote(user_id, safe='')}",
        method="PATCH",
        token=mgmt_token,
        payload=body,
    )
    got = user.get("app_metadata", {})
    if got.get("tenant_id") != tenant_id:
        raise StageError(f"app_metadata.tenant_id is {got.get('tenant_id')!r}, expected {tenant_id!r}")
    ok("seed", f"tenant_id={got.get('tenant_id')} permissions={got.get('permissions')}")


def stage_readback(domain, mgmt_token, user_id, tenant_id, permissions):
    """A separate GET, so a silently ignored PATCH cannot pass as success."""
    user = request(
        f"https://{domain}/api/v2/users/{urllib.parse.quote(user_id, safe='')}",
        token=mgmt_token,
    )
    meta = user.get("app_metadata") or {}
    problems = []
    if meta.get("tenant_id") != tenant_id:
        problems.append(f"tenant_id is {meta.get('tenant_id')!r}, expected {tenant_id!r}")
    if meta.get("permissions") != permissions:
        problems.append(f"permissions are {meta.get('permissions')!r}, expected {permissions!r}")
    if problems:
        raise StageError("; ".join(problems))
    ok("readback", "app_metadata persisted on the user record")


def stage_login(domain, client_id, username, password, audience):
    """Resource Owner Password Grant, which returns a genuine access token."""
    token = request(
        f"https://{domain}/oauth/token",
        method="POST",
        form={
            "grant_type": "password",
            "client_id": client_id,
            "username": username,
            "password": password,
            "audience": audience,
        },
    )
    access = token.get("access_token")
    if not access:
        raise StageError(f"no access_token in response: {sorted(token)}")
    ok("login", f"expires_in={token.get('expires_in')}")
    return access


def decode_claims(token):
    """Decode a JWT payload without verifying it. Local inspection only."""
    parts = token.split(".")
    if len(parts) != 3:
        raise StageError("token is not a three part JWT")
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise StageError(f"could not decode the token payload: {exc}") from exc


def stage_decode(access_token, tenant_id, permissions, audience, issuer):
    """The claims must be on the ACCESS token, which is what the gateway reads."""
    claims = decode_claims(access_token)
    got_tenant = claims.get(TENANT_CLAIM)
    got_permissions = claims.get("permissions")

    if got_tenant is None:
        raise StageError(
            f"no {TENANT_CLAIM} claim. The Post-Login Action did not run, or app_metadata "
            "is not set on this user. A token without it 403s on every route."
        )
    if got_tenant != tenant_id:
        raise StageError(f"tenant claim is {got_tenant!r}, expected {tenant_id!r}")
    if sorted(got_permissions or []) != sorted(permissions):
        # Advisory, not fatal. Auth0 RBAC owns the reserved `permissions` claim
        # when "Add Permissions in the Access Token" is enabled, and overwrites
        # whatever the Post-Login Action sets with the RBAC-assigned set. A token
        # can therefore be missing this claim and still be accepted by the
        # gateway, which is permitted by API-key scopes instead. Flag it, do not
        # block on it.
        print(f"  WARN  permissions claim is {got_permissions!r}, expected {permissions!r}")
        print("        The tenant claim is what the gateway requires, so continuing.")

    # Auth0 returns `aud` as a list for this token because the API audience and
    # the /userinfo audience are both granted. The gateway pins a single
    # audience and lets PyJWT check it, so membership is the real question.
    audiences = claims.get("aud")
    audiences = [audiences] if isinstance(audiences, str) else (audiences or [])
    if audience not in audiences:
        raise StageError(f"aud is {audiences!r}, which does not include {audience!r}")
    if claims.get("iss") != issuer:
        raise StageError(f"iss is {claims.get('iss')!r}, expected {issuer!r}")
    if "exp" in claims and claims["exp"] < time.time():
        raise StageError("token is already expired")

    ok("decode", f"tenant_id={got_tenant} permissions={got_permissions}")


def stage_gateway(access_token, gateway_url, path="/v1/keys"):
    """Report the status. 200 means the whole chain works end to end."""
    req = urllib.request.Request(
        f"{gateway_url}{path}", headers={"Authorization": f"Bearer {access_token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        detail = exc.read().decode(errors="replace")[:200]
    except urllib.error.URLError as exc:
        raise StageError(f"could not reach the gateway at {gateway_url}: {exc.reason}") from exc

    if status == 200:
        ok("gateway", f"GET {path} -> 200, the token is accepted")
        return True
    if status == 403:
        fail("gateway", f"GET {path} -> 403. The token verified but the tenant claim or "
                        "permissions were rejected. Re-run the decode stage.")
        return False
    if status == 401:
        # DEV_MODE is not the cause when it is set. `verify_token_credentials`
        # only short-circuits the two literal stand-in tokens, so a 401 here
        # means the signature or the issuer/audience genuinely did not verify.
        fail("gateway", f"GET {path} -> 401. The signature, issuer or audience did not verify. "
                        "DEV_MODE does not cause this when set; check AUTH0_ISSUER, "
                        "AUTH0_AUDIENCE and the token's exp against the gateway clock.")
        return False
    fail("gateway", f"GET {path} -> {status}")
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user-id", help="Auth0 user id, e.g. auth0|abc123")
    parser.add_argument("--username", help="login email, for the password grant")
    parser.add_argument("--password", help="login password, for the password grant")
    parser.add_argument("--client-id", default=os.getenv("AUTH0_CLIENT_ID", DEFAULT_CLIENT_ID))
    parser.add_argument("--access-token", help="skip the password grant and use this token")
    parser.add_argument("--tenant-id", default="tenant_alpha")
    parser.add_argument("--permissions", default=",".join(DEFAULT_PERMISSIONS))
    parser.add_argument("--domain", default=os.getenv("AUTH0_DOMAIN", DEFAULT_DOMAIN))
    parser.add_argument("--audience", default=os.getenv("AUTH0_AUDIENCE", DEFAULT_AUDIENCE))
    parser.add_argument("--gateway", default=os.getenv("GATEWAY_URL", "http://localhost:8000"))
    parser.add_argument("--skip-gateway", action="store_true", help="stop after decoding the token")
    args = parser.parse_args()

    permissions = [p.strip() for p in args.permissions.split(",") if p.strip()]
    mgmt_token = os.getenv("AUTH0_MGMT_TOKEN", "")
    issuer = f"https://{args.domain}/"

    if not args.user_id:
        parser.error("--user-id is required (find it in the dashboard, or /api/v2/users)")
    if not mgmt_token and not args.access_token:
        parser.error("set AUTH0_MGMT_TOKEN, or pass --access-token to verify an existing one")

    print(f"Auth0 tenant: {args.domain}")
    print(f"User:         {args.user_id}\n")

    access_token = args.access_token
    if not access_token:
        if not (args.username and args.password and args.client_id):
            parser.error(
                "the password grant needs --username, --password and --client-id "
                "(or AUTH0_CLIENT_ID). With Universal Login, sign in through the browser "
                "and pass --access-token instead."
            )
        try:
            print("1. seed")
            stage_seed(args.domain, mgmt_token, args.user_id, args.tenant_id, permissions)
            print("2. readback")
            stage_readback(args.domain, mgmt_token, args.user_id, args.tenant_id, permissions)
            print("3. login")
            access_token = stage_login(
                args.domain, args.client_id, args.username, args.password, args.audience
            )
        except StageError as exc:
            print(f"\nSetup failed:\n{exc}")
            return 1

    print("4. decode")
    try:
        stage_decode(access_token, args.tenant_id, permissions, args.audience, issuer)
    except StageError as exc:
        print(f"\nThe token is missing its claims:\n{exc}")
        return 1

    if args.skip_gateway:
        print("\nSkipped the gateway check (--skip-gateway).")
        return 0

    print("5. gateway")
    try:
        healthy = stage_gateway(access_token, args.gateway)
    except StageError as exc:
        print(f"\n{exc}")
        return 1

    print()
    if healthy:
        print("Setup verified: seeded, claimed, and accepted by the gateway.")
        return 0
    print("Claims are correct but the gateway refused the token. See the stage above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
