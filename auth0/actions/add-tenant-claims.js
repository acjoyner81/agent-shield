/**
 * Post-Login Action: stamp tenant and permission claims onto AgentShield tokens.
 *
 * WHY THIS EXISTS
 *
 * `gateway/auth.py:67` requires the namespaced claim
 * `https://api.agentshield.local/tenant_id` and answers 403
 * "Token missing mandatory tenant identification claim" without it. A plain
 * Auth0 login issues no such claim, so an authenticated portal session cannot
 * make a single successful API call. The symptom is not an auth failure: it is
 * a valid session that 403s every route, which Spec 0013 correctly refuses to
 * treat as an expired session. Users see "Permission denied" forever with no way
 * to sign in again and fix it.
 *
 * WHICH TOKEN MATTERS
 *
 * Both, and the access token is the load-bearing one. `verify_token_credentials`
 * validates the `Authorization: Bearer` credential, i.e. the ACCESS token, and
 * reads both `tenant_id` and `permissions` from it. The ID token is set as well
 * so the portal can read the tenant without a round trip and gate its UI.
 *
 * WHY `permissions` MUST BE NAMESPACED
 *
 * This was originally `api.accessToken.setCustomClaim('permissions', ...)`, and
 * that line silently did nothing. `permissions` is reserved: Auth0's own RBAC
 * owns it. Auth0's documented behaviour on a claim collision is that the login
 * transaction does not fail and the custom claim is simply not added to the
 * token. There is no error, no failed login, and nothing in the dashboard to
 * show for it.
 *
 * The result was that RBAC was not enforcing for real users. The gateway reads
 * `claims.get("permissions", [])`, Auth0 never issued the claim, every
 * permission-gated route returned 403, and `keys:write` and `tools:execute`
 * were closed to every human. The API key path was unaffected because those
 * permissions come from the key store, which is why the failure looked
 * inconsistent: machine keys worked and interactive logins did not.
 *
 * `tenant_id` was unaffected for the whole time purely because it was already
 * namespaced, which is the whole clue: one claim arrived and the other did not,
 * from the same Action, in the same login.
 *
 * The rule is that a custom claim on an ACCESS token must be namespaced, i.e.
 * carry a URI scheme. Naming it `https://api.agentshield.local/permissions`
 * makes it an opaque string to Auth0's serializer, so it is inserted as given.
 *
 * The gateway reads this exact namespaced name. Change one, change the other.
 *
 * FAILURE POLICY
 *
 * A missing `tenant_id` fails the login transaction outright rather than
 * issuing a token that cannot work. The alternative -- letting the user in and
 * letting every API call 403 -- is exactly the confusing state this Action is
 * here to eliminate, and it is much harder to diagnose from a support ticket.
 *
 * A missing `permissions` list is NOT fatal. It is set to empty, which fails
 * closed on RBAC: the user authenticates and sees a per-permission 403 naming
 * the scope they lack, instead of being locked out of the application entirely.
 * The gateway distinguishes an absent claim from an empty one and says which it
 * was, so "this token carries no permission claims at all, the Action did not
 * stamp them" is never confused with "you do not hold keys:write".
 *
 * Neither claim is derived from anything a client can influence. Both come from
 * `event.user.app_metadata`, which is only writable through the Management API
 * or the dashboard, never through a token or a user-supplied field.
 */

const CLAIM_NAMESPACE = 'https://api.agentshield.local';
const TENANT_CLAIM = `${CLAIM_NAMESPACE}/tenant_id`;
const PERMISSIONS_CLAIM = `${CLAIM_NAMESPACE}/permissions`;

exports.onExecutePostLogin = async (event, api) => {
  const metadata = event.user.app_metadata || {};
  const tenantId = metadata.tenant_id;

  if (!tenantId || typeof tenantId !== 'string') {
    // Fail the transaction. Auth0 surfaces this as an error on the login screen.
    throw new Error(
      `AgentShield: user "${event.user.sub}" has no app_metadata.tenant_id, so no token ` +
        'can be issued that the gateway will accept. Seed app_metadata before retrying.',
    );
  }

  // Accept only a real array of strings. A string here would reach
  // `_bind_tenant_state`, which does `set(permissions) if isinstance(list)` and
  // would silently yield the characters of the string as the permission set.
  let permissions = metadata.permissions;
  if (permissions === undefined || permissions === null) {
    permissions = [];
    console.log(
      `AgentShield: user "${event.user.sub}" has no app_metadata.permissions; ` +
        'issuing an empty set, which will 403 every permission-gated route.',
    );
  } else if (!Array.isArray(permissions) || permissions.some((p) => typeof p !== 'string')) {
    throw new Error(
      'AgentShield: app_metadata.permissions must be an array of strings ' +
        `(got ${typeof permissions}). The gateway compares membership directly.`,
    );
  }

  api.idToken.setCustomClaim(TENANT_CLAIM, tenantId);
  api.idToken.setCustomClaim(PERMISSIONS_CLAIM, permissions);

  api.accessToken.setCustomClaim(TENANT_CLAIM, tenantId);
  api.accessToken.setCustomClaim(PERMISSIONS_CLAIM, permissions);
};