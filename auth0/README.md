# Auth0 tenant configuration

Tenant: `dev-zymaiayb0afkpn7n.us.auth0.com` — API audience `https://api.agentshield.local`

Dashboard-side configuration is not reproducible from this repo, so it lives here as
code plus a setup contract. If you change the Action, change it here and redeploy;
do not edit it only in the dashboard.

## Actions

| File | Trigger | What it does |
| --- | --- | --- |
| `actions/add-tenant-claims.js` | Post-Login | Stamps `https://agentshield.com/tenant_id` and `permissions` onto the ID and access tokens |

### Why it is required

`gateway/auth.py:67` reads `https://agentshield.com/tenant_id` from the **access
token** and returns 403 `Token missing mandatory tenant identification claim`
when it is absent. A stock Auth0 login issues no such claim, so an authenticated
portal session makes zero successful API calls.

The failure is genuinely confusing without this Action, and worth understanding
before you remove it:

- The session is real and lasts 24h, so nothing looks expired.
- Every call returns **403**, not 401. Per Spec 0013 a 403 must never trigger
  re-authentication, so the app correctly declines to send the user back to Auth0.
- The user is therefore stuck on "Permission denied" with no way to recover by
  signing in again. Only seeding `app_metadata` and re-authenticating fixes it.

### Install

1. Dashboard → **Actions → Create Action → Build a custom Action**.
2. Name `add-tenant-claims`, runtime **Node 18**.
3. Replace the template body with the contents of `actions/add-tenant-claims.js`.
4. **Deploy**, then add it to the **Login** trigger.

### Verify it is actually live

The Access token for the API audience is what the gateway reads, so decode that —
not the ID token:

```bash
# Management API, needs a token with update:users on the target user
curl -s -X PATCH \
  "https://dev-zymaiayb0afkpn7n.us.auth0.com/api/v2/users/<USER_ID>" \
  -H "Authorization: Bearer $MGMT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"app_metadata":{"tenant_id":"tenant_alpha","permissions":["tools:execute","keys:write","billing:admin"]}}'
```

Seeded with the enforced set only. `logs:read` is intentionally absent because
nothing checks it; add it if you are reproducing a `DEV_MODE` fixture.

Then sign in and confirm the claims are present on the access token:

```bash
# paste the access_token from the SPA's localStorage entry
echo "$ACCESS_TOKEN" | cut -d. -f2 | base64 -d | jq '.["https://agentshield.com/tenant_id"], .permissions'
```

Expected:

```json
"tenant_alpha"
[ "tools:execute", "keys:write", "billing:admin" ]
```

A gateway call should then stop 403ing:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  http://localhost:8000/api/v1/keys
```

## Permissions vocabulary

`gateway/auth.py` compares these strings directly via `require_permission`, so the
spelling is a contract, not a convention. The two kinds behave differently and
conflating them would mislead you when debugging a 403.

**Hard gates — absent means 403 on the route:**

| Permission | Guards |
| --- | --- |
| `tools:execute` | `POST /v1/tools/execute` (`main.py:427`) |
| `keys:write` | `POST` key create (`keys.py:182`), rotate (`keys.py:252`), revoke (`keys.py:339`) |

**Capability flag — absent means a field is omitted, not a 403:**

| Permission | Effect |
| --- | --- |
| `billing:admin` | Sets `include_cost` on the usage summary (`metering.py:393`). Without it a tenant sees token counts but not spend |

**Not enforced anywhere:**

`logs:read` appears only in the `DEV_MODE` mock tokens at `auth.py:48` and `:54`.
No route checks it. Telemetry reads (`GET /v1/telemetry/logs`, `main.py:507`) are
protected by tenant isolation alone, not by a permission. So granting `logs:read`
does nothing and omitting it blocks nothing. It is carried in the dev tokens
because it reads as though it guards logs, and that impression is wrong — worth
knowing before you rely on it to hide telemetry from a user.

`add-tenant-claims.js` deliberately does **not** invent defaults for this list. A
missing list yields an empty set, so RBAC fails closed and the resulting 403 names
the missing scope. Granting a plausible-looking default set instead would hand out
`keys:write` to anyone whose metadata was half-seeded.

## Tenant bootstrap

`app_metadata` is per user, so multi-tenant routing is currently one tenant per
user. If a user needs access to more than one tenant, that is a modelling decision
(`tenant_ids` array plus an active-tenant claim) and the gateway's single-tenant
assumption in `_bind_tenant_state` has to change with it. Not assumed here.