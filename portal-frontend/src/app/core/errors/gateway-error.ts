/**
 * The named failure shapes the gateway can produce, and the one shape they
 * collapse into before anything is rendered.
 *
 * The taxonomy is a closed string union rather than a class per kind on
 * purpose. `classifyGatewayError` maps status codes onto it through a
 * `Record<GatewayErrorKind, string>` of generic copy, so adding a seventh kind
 * without deciding its wording is a compile error rather than an unhandled
 * branch that renders `undefined` to a tenant. `instanceof` narrowing cannot
 * give that guarantee.
 *
 * This file has no Angular dependency and no side effects. It is data only.
 */

export type GatewayErrorKind =
  | 'budget_exhausted'   // 402
  | 'rate_limited'       // 429
  | 'guardrail_blocked'  // 400
  | 'scope_denied'       // 403
  | 'session_expired'    // 401
  | 'unexpected';        // 5xx, network, anything unmapped

export interface GatewayBudget {
  currentSpendUsd: number;
  maxBudgetUsd: number;
}

export interface GatewayError {
  /** The closed taxonomy above. Drives every display decision. */
  kind: GatewayErrorKind;
  /** Already user readable. Never "[object Object]", never a raw field name. */
  message: string;
  /** 0 when the request never reached the gateway. */
  status: number;
  /** 429 only. Absent when the header was missing or unparseable. */
  retryAfterSeconds?: number;
  /** 429 only, from `X-RateLimit-Remaining`. */
  remaining?: number;
  /** 402 only, when the gateway sent both figures. */
  budget?: GatewayBudget;
  /**
   * The raw `HttpErrorResponse`, for devtools inspection only.
   *
   * It can carry the whole response body, so nothing may render it and no
   * message may be derived from it. Typed `unknown` so that reading anything
   * off it is a deliberate act.
   */
  originalError?: unknown;
}
