import { HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';

import { classifyGatewayError } from './classify';
import { GatewayErrorKind } from './gateway-error';

/**
 * The taxonomy, pinned against the three real `detail` shapes.
 *
 * The shapes are not hypothetical: they are asserted in
 * `gateway/tests/test_portal_integration.py`. A string from the portal facing
 * 402, a dict from `completion_proxy` and from the enriched portal 402, and
 * FastAPI's list of validation objects from a 422.
 *
 * This file needs no TestBed. The classifier has no dependencies to provide,
 * which is the property that makes it exhaustively testable.
 */

/**
 * Builds the response the gateway actually produces.
 *
 * `body` is the serialized response body, so a FastAPI `HTTPException` arrives
 * wrapped as `{ detail: ... }`. The tests below pass that envelope rather than a
 * bare detail on purpose: `HttpErrorResponse.error` is the body, and reading it
 * as if it were the detail is a bug that a hand-built bare string would hide.
 */
function httpError(status: number, body: unknown, headers: Record<string, string> = {}): HttpErrorResponse {
  return new HttpErrorResponse({
    status,
    statusText: '',
    url: '/api/v1/usage/summary',
    error: body,
    headers: new HttpHeaders(headers),
  });
}

/** The shape FastAPI produces for `HTTPException(status_code=403, detail="...")`. */
function fastapiException(detail: unknown): unknown {
  return { detail };
}

function expectKind(result: ReturnType<typeof classifyGatewayError>, kind: GatewayErrorKind): void {
  expect(result).not.toBeNull();
  expect(result!.kind).toBe(kind);
}

describe('classifyGatewayError', () => {
  describe('non gateway failures', () => {
    it('returns null for a plain Error, so the interceptor passes it through', () => {
      expect(classifyGatewayError(new Error('boom'))).toBeNull();
    });

    it('returns null for undefined and for arbitrary objects', () => {
      expect(classifyGatewayError(undefined)).toBeNull();
      expect(classifyGatewayError(null)).toBeNull();
      expect(classifyGatewayError({ status: 402, error: {} })).toBeNull();
    });
  });

  describe('status to kind', () => {
    it('maps every status the gateway raises to its named kind', () => {
      expectKind(classifyGatewayError(httpError(400, 'blocked')), 'guardrail_blocked');
      expectKind(classifyGatewayError(httpError(401, 'expired')), 'session_expired');
      expectKind(classifyGatewayError(httpError(402, 'no money')), 'budget_exhausted');
      expectKind(classifyGatewayError(httpError(403, 'nope')), 'scope_denied');
      expectKind(classifyGatewayError(httpError(429, 'slow down')), 'rate_limited');
    });

    it('maps a 5xx to unexpected', () => {
      expectKind(classifyGatewayError(httpError(500, 'server on fire')), 'unexpected');
      expectKind(classifyGatewayError(httpError(503, null)), 'unexpected');
    });

    it('maps status 0, an offline client, to unexpected and keeps status 0', () => {
      const result = classifyGatewayError(httpError(0, null));
      expectKind(result, 'unexpected');
      expect(result!.status).toBe(0);
    });

    it('maps an unmapped 4xx to unexpected rather than guessing', () => {
      expectKind(classifyGatewayError(httpError(418, 'teapot')), 'unexpected');
    });
  });

  describe('the FastAPI envelope', () => {
    // The gateway raises every refusal via HTTPException, so the body is
    // `{"detail": ...}`. This is the case a bare-string test body hides.
    it('unwraps a string detail from the envelope', () => {
      const result = classifyGatewayError(
        httpError(403, fastapiException('Key creation requires keys:write')),
      );
      expect(result!.message).toBe('Key creation requires keys:write');
    });

    it('unwraps the budget dict from the envelope', () => {
      const result = classifyGatewayError(
        httpError(402, fastapiException({ current_spend_usd: 3.2, max_budget_usd: 2 })),
      );
      expect(result!.budget).toEqual({ currentSpendUsd: 3.2, maxBudgetUsd: 2 });
      expect(result!.message).toBe('Budget exhausted: $3.20 spent of $2.00 today.');
    });

    it('unwraps a 422 validation list from the envelope', () => {
      const result = classifyGatewayError(
        httpError(
          422,
          fastapiException([{ loc: ['body', 'tier'], msg: 'Input should be a valid string' }]),
        ),
      );
      expect(result!.message).toBe('tier: Input should be a valid string');
    });

    it('passes an already unwrapped body through, so a non gateway producer still works', () => {
      expect(classifyGatewayError(httpError(403, 'bare string'))!.message).toBe('bare string');
    });

    it('does not mistake a body that merely has a detail key for a real one', () => {
      // detail is null, so there is nothing useful, and the generic copy applies.
      const result = classifyGatewayError(httpError(403, { detail: null }));
      expect(result!.message).toBe('Your account does not have permission for this action.');
    });
  });

  describe('402 budget extraction', () => {
    // gateway/llm_proxy.py raises exactly these three keys.
    const body = {
      error: 'Tenant budget limit exceeded',
      current_spend_usd: 3.2,
      max_budget_usd: 2.0,
    };

    it('reads both figures off the dict the gateway actually sends', () => {
      const result = classifyGatewayError(httpError(402, body));
      expect(result!.budget).toEqual({ currentSpendUsd: 3.2, maxBudgetUsd: 2 });
    });

    it('composes a message carrying the figures, because the dict has no message key', () => {
      const result = classifyGatewayError(httpError(402, body));
      expect(result!.message).toBe('Budget exhausted: $3.20 spent of $2.00 today.');
    });

    it('falls back to generic copy when a figure is missing, never printing undefined', () => {
      const result = classifyGatewayError(httpError(402, { error: 'nope', current_spend_usd: 3.2 }));
      expect(result!.budget).toBeUndefined();
      expect(result!.message).toBe('Daily budget exhausted. Requests pause until the budget resets.');
    });

    it('rejects a non numeric figure rather than rendering NaN', () => {
      const result = classifyGatewayError(
        httpError(402, { current_spend_usd: 'lots', max_budget_usd: 2 }),
      );
      expect(result!.budget).toBeUndefined();
      expect(result!.message).not.toContain('NaN');
    });

    it('accepts zero spend, which is a real value not a missing one', () => {
      const result = classifyGatewayError(
        httpError(402, { current_spend_usd: 0, max_budget_usd: 2 }),
      );
      expect(result!.budget).toEqual({ currentSpendUsd: 0, maxBudgetUsd: 2 });
    });

    it('still uses a bare string detail for the pre enrichment portal 402', () => {
      const result = classifyGatewayError(httpError(402, 'Tenant daily budget exceeded'));
      expect(result!.message).toBe('Tenant daily budget exceeded');
      expect(result!.budget).toBeUndefined();
    });
  });

  describe('429 rate limit headers', () => {
    it('reads Retry-After, the header the gateway always writes as integer seconds', () => {
      const result = classifyGatewayError(httpError(429, 'slow down', { 'Retry-After': '10' }));
      expect(result!.retryAfterSeconds).toBe(10);
    });

    it('reads X-RateLimit-Remaining', () => {
      const result = classifyGatewayError(httpError(429, 'slow down', { 'X-RateLimit-Remaining': '0' }));
      expect(result!.remaining).toBe(0);
    });

    it('omits retryAfterSeconds when the header is absent rather than guessing a duration', () => {
      const result = classifyGatewayError(httpError(429, 'slow down'));
      expect(result!.retryAfterSeconds).toBeUndefined();
    });

    it('omits retryAfterSeconds when the header is not a number', () => {
      const result = classifyGatewayError(
        httpError(429, 'slow down', { 'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT' }),
      );
      expect(result!.retryAfterSeconds).toBeUndefined();
    });

    it('tolerates a missing headers object entirely', () => {
      const response = new HttpErrorResponse({ status: 429, error: null });
      const result = classifyGatewayError(response);
      expect(result!.retryAfterSeconds).toBeUndefined();
      expect(result!.remaining).toBeUndefined();
      expect(result!.message).toBe('Rate limit reached. Requests are being throttled.');
    });

    it('prefers a useful string body over the generic copy', () => {
      expect(classifyGatewayError(httpError(429, 'slow down'))!.message).toBe('slow down');
    });
  });

  describe('message extraction precedence', () => {
    it('step 1: uses a string detail verbatim', () => {
      expect(classifyGatewayError(httpError(403, 'Missing scope: keys:write'))!.message).toBe(
        'Missing scope: keys:write',
      );
    });

    it('step 2: uses a message key on an object detail', () => {
      expect(classifyGatewayError(httpError(403, { message: 'nope' }))!.message).toBe('nope');
    });

    it('step 3: joins a 422 validation list with its field names', () => {
      const body = [
        { loc: ['body', 'messages'], msg: 'Input should be a valid list', type: 'list_type' },
      ];
      expect(classifyGatewayError(httpError(422, body))!.message).toBe(
        'messages: Input should be a valid list',
      );
    });

    it('step 3: drops numeric loc segments so an array index is not the field name', () => {
      const body = [
        { loc: ['body', 'messages', 0, 'content'], msg: 'Field required', type: 'missing' },
      ];
      expect(classifyGatewayError(httpError(422, body))!.message).toBe('content: Field required');
    });

    it('step 3: joins several validation problems into one readable line', () => {
      const body = [
        { loc: ['body', 'tier'], msg: 'Input should be a valid string', type: 'string_type' },
        { loc: ['body', 'seats'], msg: 'Input should be greater than 0', type: 'greater_than' },
      ];
      expect(classifyGatewayError(httpError(422, body))!.message).toBe(
        'tier: Input should be a valid string seats: Input should be greater than 0',
      );
    });

    it('step 5: uses the per status generic copy when the body says nothing useful', () => {
      expect(classifyGatewayError(httpError(400, null))!.message).toBe(
        'That request was blocked by a safety rule.',
      );
      expect(classifyGatewayError(httpError(401, ''))!.message).toBe(
        'Your session expired. Signing you back in.',
      );
      expect(classifyGatewayError(httpError(403, {}))!.message).toBe(
        'Your account does not have permission for this action.',
      );
    });

    it('never returns an empty or object string for any shape', () => {
      const bodies: unknown[] = [null, undefined, '', {}, [], [{}], { weird: 1 }, 42, true];
      for (const body of bodies) {
        for (const status of [400, 401, 402, 403, 429, 500]) {
          const message = classifyGatewayError(httpError(status, body))!.message;
          expect(message).toBeTruthy();
          expect(message).not.toContain('[object Object]');
        }
      }
    });

    it('never returns undefined from the function itself', () => {
      expect(classifyGatewayError(httpError(418, 'teapot'))).not.toBeUndefined();
    });
  });

  describe('originalError', () => {
    it('retains the raw response for devtools inspection', () => {
      const response = httpError(402, 'no money');
      expect(classifyGatewayError(response)!.originalError).toBe(response);
    });
  });
});
