/**
 * Client Credentials Exchange Action: stamp tenant and permission claims onto
 * AgentShield machine-to-machine (M2M) access tokens.
 *
 * WHY THIS EXISTS
 *
 * `add-tenant-claims.js` is a Post-Login Action, and Post-Login does not run for
 * the client credentials grant. A machine to machine token therefore arrives
 * with no `https://api.agentshield.local/tenant_id` claim, and `gateway/auth.py`
 * answers 403 "Token missing mandatory tenant identification claim" on every
 * route. The token is real and the signature verifies -- it is simply missing
 * the claim the gateway requires -- so the failure looks like a permissions
 * problem rather than a missing extension point.
 *
 * The client credentials grant has its own extensibility point: the Client
 * Credentials Exchange trigger, handled by `onExecuteCredentialsExchange`.
 * This Action is the machine to machine counterpart to `add-tenant-claims.js`.
 *
 * WHICH PROPERTY HOLDS THE METADATA
 *
 * On the Client Credentials Exchange event the client metadata is
 * `event.client.metadata`. It is NOT `event.client.client_metadata` -- that name
 * belongs to the Rules and Hooks world -- and it is NOT `event.metadata`. Reading
 * the wrong path yields `undefined`, the claim is never stamped, and the token
 * 403s exactly as if this Action were absent.
 *
 * WHICH TOKEN MATTERS
 *
 * Only the access token. A machine to machine exchange issues no ID token, and
 * `verify_token_credentials` validates the `Authorization: Bearer` credential,
 * i.e. the access token, reading both claims from it.
 *
 * NAMESPACING
 *
 * Both claims are namespaced for the same reason documented at length in
 * `add-tenant-claims.js`: the bare `permissions` name is reserved by Auth0's own
 * RBAC and a custom claim under that name is silently dropped without failing
 * the exchange.
 *
 * FAILURE POLICY
 *
 * `tenant_id` falls back to `tenant_alpha` so the development tenant always
 * receives a usable token. In a multi tenant deployment drop that fallback and
 * require `event.client.metadata.tenant_id`, so a misconfigured client fails
 * closed at the exchange instead of landing in a default tenant.
 *
 * The permission set is the enforced set from `gateway/auth.py`. It is fixed in
 * code rather than read from `event.client.metadata`, because the metadata value
 * a client can carry is a string and the gateway compares list membership
 * directly; move it to metadata only alongside JSON parsing and a validation
 * step.
 *
 * DEPLOYMENT
 *
 * Auth0 dashboard: Actions -> Library -> Build Custom, trigger "Machine To
 * Machine / Client Credentials Exchange", runtime Node 18+, paste this file,
 * Deploy, then bind it to the Credentials Exchange trigger. Per the repository
 * contract (`auth0/README.md`), change the Action here and redeploy; never edit
 * it only in the dashboard.
 */

const CLAIM_NAMESPACE = 'https://api.agentshield.local';
const TENANT_CLAIM = `${CLAIM_NAMESPACE}/tenant_id`;
const PERMISSIONS_CLAIM = `${CLAIM_NAMESPACE}/permissions`;
const DEFAULT_TENANT = 'tenant_alpha';
const ENFORCED_PERMISSIONS = ['tools:execute', 'keys:write', 'billing:admin', 'telemetry:admin'];

exports.onExecuteCredentialsExchange = async (event, api) => {
  // `event.client.metadata`, not `event.client.client_metadata` (Rules/Hooks) and
  // not `event.metadata`. See the header note.
  const metadata = (event.client && event.client.metadata) || {};
  const tenantId = metadata.tenant_id || DEFAULT_TENANT;

  api.accessToken.setCustomClaim(TENANT_CLAIM, tenantId);
  api.accessToken.setCustomClaim(PERMISSIONS_CLAIM, ENFORCED_PERMISSIONS);
};
