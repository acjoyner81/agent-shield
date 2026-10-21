/**
 * The permission scopes this gateway actually enforces.
 *
 * Every entry is read by a customer on the keys page, so each one names the
 * route or response field that proves the check exists. A scope listed here that
 * no route reads would be the same lie as a green "PII redaction: Enabled" row,
 * which is why `logs:read` is absent: tokens carry it and nothing checks it.
 *
 * Keep this list in step with `require_permission(...)` under `gateway/` and the
 * `billing:admin` cost gate in `gateway/metering.py`.
 */
export interface ScopeDefinition {
  /** The exact string a token claim or key record must contain. */
  readonly scope: string;
  /** The live control the scope opens, in one sentence. */
  readonly effect: string;
}

export const ENFORCED_SCOPES: readonly ScopeDefinition[] = [
  {
    scope: 'tools:execute',
    effect: 'Executes tenant tools through POST /v1/tools/execute.',
  },
  {
    scope: 'keys:write',
    effect: 'Creates, rotates, revokes, and rescopes API keys.',
  },
  {
    scope: 'telemetry:admin',
    effect: 'Replays dead-lettered telemetry through POST /v1/telemetry/redrive.',
  },
  {
    scope: 'billing:admin',
    effect: 'Reveals the cost fields /v1/usage/summary leaves null for everyone else.',
  },
];

/**
 * The scope list for a key row, always a string.
 *
 * An empty set renders as "none" rather than a blank, because a key that can
 * authenticate but reach no gated route is a fact a tenant admin should see.
 */
export function describeScopes(permissions: readonly string[] | null | undefined): string {
  return permissions && permissions.length > 0 ? permissions.join(', ') : 'none';
}
