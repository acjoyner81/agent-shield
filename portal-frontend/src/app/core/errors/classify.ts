import { HttpErrorResponse } from '@angular/common/http';
import { GatewayBudget, GatewayError, GatewayErrorKind } from './gateway-error';

/**
 * The one place a gateway failure becomes a named, user-readable error.
 *
 * Pure: no `inject`, no service, no browser globals, no network. Every branch is
 * decided by the response alone, so this can be tested exhaustively without a
 * TestBed. It never throws and never returns `undefined`; a shape it does not
 * recognize still produces a `GatewayError`, because the alternative is a
 * banner that renders nothing, which is the failure mode this whole standard
 * exists to remove.
 */

/**
 * What to say when the response body is not useful.
 *
 * A `Record` over the closed union, not a `switch`. This is the compile time
 * enforcement the standard leans on instead of a lint rule: adding a kind to
 * `GatewayErrorKind` without deciding its wording fails to compile here.
 */
const GENERIC_COPY: Record<GatewayErrorKind, string> = {
  guardrail_blocked: 'That request was blocked by a safety rule.',
  session_expired: 'Your session expired. Signing you back in.',
  budget_exhausted: 'Daily budget exhausted. Requests pause until the budget resets.',
  scope_denied: 'Your account does not have permission for this action.',
  rate_limited: 'Rate limit reached. Requests are being throttled.',
  unexpected: 'Something went wrong reaching the gateway. Try again.',
};

/** Status to kind. Anything absent is `unexpected`, by definition. */
const STATUS_TO_KIND: Record<number, GatewayErrorKind> = {
  400: 'guardrail_blocked',
  401: 'session_expired',
  402: 'budget_exhausted',
  403: 'scope_denied',
  429: 'rate_limited',
};

const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

/**
 * Reach the real detail out of FastAPI's envelope.
 *
 * `HttpErrorResponse.error` is the parsed response *body*, and every refusal in
 * this gateway is raised as `HTTPException`, which FastAPI serialises as
 * `{"detail": ...}`. So the detail is one level down, not the body itself.
 *
 * Without this, a `{"detail": "Key creation requires keys:write"}` body arrives as
 * an object with no `message` key, falls through the whole precedence, and the
 * user reads the generic copy while the gateway's specific explanation sits
 * unread in the response. Anything already unwrapped, or any body with no
 * `detail` key, is passed through untouched so the precedence below still sees
 * exactly what it was written for.
 */
function unwrapDetail(body: unknown): unknown {
  if (isRecord(body) && 'detail' in body) {
    return body['detail'];
  }
  return body;
}

/** A real, finite number, or nothing. Guards every figure that reaches a banner. */
function finiteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function isDevelopment(): boolean {
  return typeof ngDevMode === 'undefined' || !!ngDevMode;
}

function readHeader(response: HttpErrorResponse, name: string): string | null {
  return response.headers.get(name);
}

/**
 * The 402 branch.
 *
 * `completion_proxy` raises `{ error, current_spend_usd, max_budget_usd }`
 * (`gateway/llm_proxy.py`), and there is no `message` key, so the general
 * precedence below would discard the body at its "object without message" step
 * and render generic copy while the two figures the gateway sent specifically
 * for this went unread. It runs ahead of the fall-through, for 402 only.
 *
 * Returns null when either figure is missing or not a number, so a malformed
 * body degrades to the generic copy instead of printing "spent of $undefined".
 */
function extractBudget(detail: unknown): GatewayBudget | null {
  if (!isRecord(detail)) {
    return null;
  }
  const currentSpendUsd = finiteNumber(detail['current_spend_usd']);
  const maxBudgetUsd = finiteNumber(detail['max_budget_usd']);
  if (currentSpendUsd === undefined || maxBudgetUsd === undefined) {
    return null;
  }
  return { currentSpendUsd, maxBudgetUsd };
}

function formatBudgetMessage(budget: GatewayBudget): string {
  return `Budget exhausted: ${USD.format(budget.currentSpendUsd)} spent of ${USD.format(budget.maxBudgetUsd)} today.`;
}

/**
 * FastAPI's 422 shape: a list of `{ loc, msg, type }`.
 *
 * `loc` for a body field is `["body", "messages"]`. Numeric segments are array
 * indexes, so they are dropped and the last named one is used, which keeps the
 * message pointing at the field the developer has to fix.
 */
function formatValidationList(detail: unknown[]): string | null {
  const parts: string[] = [];
  for (const entry of detail) {
    if (!isRecord(entry)) {
      continue;
    }
    const msg = entry['msg'];
    if (typeof msg !== 'string' || msg.length === 0) {
      continue;
    }
    const loc = entry['loc'];
    if (!Array.isArray(loc)) {
      parts.push(msg);
      continue;
    }
    const named = loc.filter(
      (segment): segment is string => typeof segment === 'string' && segment.length > 0,
    );
    parts.push(named.length > 0 ? `${named[named.length - 1]}: ${msg}` : msg);
  }
  return parts.length > 0 ? parts.join(' ') : null;
}

/**
 * The message precedence, in order, stopping at the first hit.
 *
 * 1. `detail` is a string.
 * 2. `detail` is an object with a `message` string.
 * 3. `detail` is a list of validation objects.
 * 4. `detail` is an object without `message`: fall through.
 * 5. The per status generic copy.
 */
function extractMessage(detail: unknown, kind: GatewayErrorKind): string {
  if (typeof detail === 'string' && detail.length > 0) {
    return detail;
  }
  if (isRecord(detail)) {
    const message = detail['message'];
    if (typeof message === 'string' && message.length > 0) {
      return message;
    }
  }
  if (Array.isArray(detail)) {
    const joined = formatValidationList(detail);
    if (joined !== null) {
      return joined;
    }
  }
  if (isDevelopment()) {
    // Naming the keys is what makes a new body shape noticeable in development
    // instead of silently rendering the generic copy forever.
    const seen = isRecord(detail) ? Object.keys(detail).join(', ') : typeof detail;
    console.warn(`[gateway-error] unrecognised detail shape for ${kind}: ${seen}`);
  }
  return GENERIC_COPY[kind];
}

/**
 * `Retry-After` is integer seconds here: `rate_limit.py` writes it on every
 * 429 as a positive whole number. When it is missing or unparseable the field is
 * omitted rather than guessed, and the banner clears on the next success alone.
 */
function extractRetryAfter(response: HttpErrorResponse): number | undefined {
  const raw = readHeader(response, 'Retry-After');
  if (raw === null) {
    return undefined;
  }
  const parsed = Number.parseInt(raw.trim(), 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return undefined;
  }
  return parsed;
}

function extractRemaining(response: HttpErrorResponse): number | undefined {
  const raw = readHeader(response, 'X-RateLimit-Remaining');
  if (raw === null) {
    return undefined;
  }
  const parsed = Number.parseInt(raw.trim(), 10);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/**
 * Normalize a failed gateway call.
 *
 * Returns null when the argument is not an `HttpErrorResponse`, which tells the
 * interceptor to pass the failure through untouched: a non gateway HTTP call
 * must never be reported as a gateway outage.
 *
 * `HttpErrorResponse` is a plain data class, so importing it keeps this
 * function pure and testable without dragging in the injector. The one thing
 * that makes this file depend on Angular at all is the reliable type check,
 * and a structural guess at "does this look like an HTTP error" would be a lie
 * waiting for a backend that returns a differently shaped object.
 */
export function classifyGatewayError(err: unknown): GatewayError | null {
  if (!(err instanceof HttpErrorResponse)) {
    return null;
  }

  const kind = STATUS_TO_KIND[err.status] ?? 'unexpected';
  const detail = unwrapDetail(err.error);

  const classified: GatewayError = {
    kind,
    message: extractMessage(detail, kind),
    status: err.status,
    originalError: err,
  };

  if (kind === 'budget_exhausted') {
    const budget = extractBudget(detail);
    if (budget !== null) {
      classified.budget = budget;
      classified.message = formatBudgetMessage(budget);
    }
  }

  if (kind === 'rate_limited') {
    const retryAfterSeconds = extractRetryAfter(err);
    if (retryAfterSeconds !== undefined) {
      classified.retryAfterSeconds = retryAfterSeconds;
    }
    const remaining = extractRemaining(err);
    if (remaining !== undefined) {
      classified.remaining = remaining;
    }
  }

  return classified;
}
