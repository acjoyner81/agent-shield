import { TestBed } from '@angular/core/testing';
import { HttpErrorResponse, HttpHeaders } from '@angular/common/http';

import { NotificationService, StaleWidget } from './error-state.service';
import { GatewayError } from './gateway-error';

/**
 * The state rules, pinned where the coalescing decisions live.
 *
 * The two that matter most and are easiest to get wrong:
 * - A `silent` poll failure must raise nothing, or a 429 from the 60 second
 *   dashboard poller interrupts a user who did nothing.
 * - A `silent` poll failure must still report a 401, or a session that died while
 *   the tab was idle never redirects, because the poll is the only request left.
 */

function gatewayError(overrides: Partial<GatewayError> = {}): GatewayError {
  return {
    kind: 'unexpected',
    message: 'Something went wrong.',
    status: 500,
    ...overrides,
  };
}

describe('NotificationService', () => {
  let service: NotificationService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(NotificationService);
  });

  afterEach(() => {
    service.ngOnDestroy();
  });

  describe('silent polls', () => {
    it('raises nothing at all for a failed background poll', () => {
      service.report(gatewayError({ kind: 'rate_limited' }), 'silent', 'usage');
      expect(service.hasAnyNotice()).toBe(false);
      expect(service.budgetBanner()).toBeNull();
      expect(service.rateLimit()).toBeNull();
      expect(service.toast()).toBeNull();
    });

    it('marks the widget stale so the read can show a quiet note', () => {
      service.report(gatewayError(), 'silent', 'usage');
      expect(service.isStale('usage')).toBe(true);
    });

    it('does not mark a widget it was not told about', () => {
      service.report(gatewayError(), 'silent');
      expect(service.isStale('usage')).toBe(false);
      expect(service.isStale('health')).toBe(false);
    });

    it('leaves other widgets alone, so one dead poll does not mark the page stale', () => {
      service.report(gatewayError(), 'silent', 'health');
      expect(service.isStale('health')).toBe(true);
      expect(service.isStale('usage')).toBe(false);
    });

    it('still reports a 401, because the poll may be the only signal', () => {
      service.report(gatewayError({ kind: 'session_expired', status: 401 }), 'silent', 'usage');
      expect(service.sessionExpired()).toBe(true);
    });

    it('does not let the 401 override also mark the widget stale', () => {
      service.report(gatewayError({ kind: 'session_expired' }), 'silent', 'usage');
      expect(service.isStale('usage')).toBe(false);
    });
  });

  describe('budget banner, 402', () => {
    it('shows the banner carrying the figures', () => {
      service.report(
        gatewayError({
          kind: 'budget_exhausted',
          status: 402,
          message: 'Budget exhausted: $3.20 spent of $2.00 today.',
          budget: { currentSpendUsd: 3.2, maxBudgetUsd: 2 },
        }),
        'banner',
      );
      expect(service.budgetBanner()!.message).toBe('Budget exhausted: $3.20 spent of $2.00 today.');
    });

    it('dismisses per banner, not as a mute: the next 402 returns it', () => {
      const error = gatewayError({ kind: 'budget_exhausted' });
      service.report(error, 'banner');
      service.dismissBudgetBanner();
      expect(service.budgetBanner()).toBeNull();

      service.report(error, 'banner');
      expect(service.budgetBanner()).not.toBeNull();
    });

    it('updates in place rather than stacking when a second 402 arrives', () => {
      service.report(gatewayError({ kind: 'budget_exhausted', message: 'first' }), 'banner');
      service.report(gatewayError({ kind: 'budget_exhausted', message: 'second' }), 'banner');
      expect(service.budgetBanner()!.message).toBe('second');
    });
  });

  describe('rate limit, 429', () => {
    it('sets a countdown from Retry-After', () => {
      service.report(
        gatewayError({ kind: 'rate_limited', status: 429, retryAfterSeconds: 30 }),
        'banner',
      );
      expect(service.rateLimit()!.secondsRemaining).toBe(30);
    });

    it('keeps the remaining header for display', () => {
      service.report(
        gatewayError({ kind: 'rate_limited', retryAfterSeconds: 5, remaining: 0 }),
        'banner',
      );
      expect(service.rateLimit()!.remaining).toBe(0);
    });

    it('coalesces a second 429 instead of stacking a second banner', () => {
      service.report(gatewayError({ kind: 'rate_limited', retryAfterSeconds: 10 }), 'banner');
      service.report(gatewayError({ kind: 'rate_limited', retryAfterSeconds: 25 }), 'banner');
      expect(service.rateLimit()!.secondsRemaining).toBe(25);
    });

    it('clears on the first success, without waiting out the full window', () => {
      service.report(gatewayError({ kind: 'rate_limited', retryAfterSeconds: 60 }), 'banner');
      expect(service.rateLimit()).not.toBeNull();
      service.reportSuccess();
      expect(service.rateLimit()).toBeNull();
    });

    it('sits at zero rather than guessing a duration when Retry-After is absent', () => {
      service.report(gatewayError({ kind: 'rate_limited' }), 'banner');
      expect(service.rateLimit()!.secondsRemaining).toBe(0);
    });

    it('is safe to clear when nothing is set', () => {
      expect(() => service.reportSuccess()).not.toThrow();
    });
  });

  describe('session expiry, 401', () => {
    it('sets the one-shot guard', () => {
      service.report(gatewayError({ kind: 'session_expired' }), 'banner');
      expect(service.sessionExpired()).toBe(true);
    });

    it('can be acknowledged so a later expiry can redirect again', () => {
      service.report(gatewayError({ kind: 'session_expired' }), 'banner');
      service.acknowledgeSessionExpiry();
      expect(service.sessionExpired()).toBe(false);
    });

    it('raises no banner, since the redirect is the whole message', () => {
      service.report(gatewayError({ kind: 'session_expired' }), 'banner');
      expect(service.budgetBanner()).toBeNull();
      expect(service.toast()).toBeNull();
    });
  });

  describe('toast', () => {
    it('shows a 403 as a transient toast', () => {
      service.report(
        gatewayError({ kind: 'scope_denied', status: 403, message: 'Missing scope: keys:write' }),
        'banner',
      );
      expect(service.toast()!.message).toBe('Missing scope: keys:write');
    });

    it('shows a 5xx as a transient toast', () => {
      service.report(gatewayError({ kind: 'unexpected', status: 500 }), 'toast');
      expect(service.toast()).not.toBeNull();
    });

    it('replaces rather than stacks when a second toast arrives', () => {
      service.report(gatewayError({ kind: 'unexpected', message: 'first' }), 'toast');
      service.report(gatewayError({ kind: 'unexpected', message: 'second' }), 'toast');
      expect(service.toast()!.message).toBe('second');
    });

    it('can be dismissed by hand', () => {
      service.report(gatewayError(), 'toast');
      service.dismissToast();
      expect(service.toast()).toBeNull();
    });
  });

  describe('staleness', () => {
    const widget: StaleWidget = 'logs';

    it('never clears the last known values, only the stale flag', () => {
      service.markFresh(widget);
      service.markStale(widget);
      expect(service.isStale(widget)).toBe(true);
      expect(service.lastUpdatedAt(widget)).not.toBeNull();
    });

    it('clears the stale flag and stamps the time on a fresh fetch', () => {
      service.markStale(widget);
      service.markFresh(widget);
      expect(service.isStale(widget)).toBe(false);
    });

    it('has no last updated time before the first success', () => {
      expect(service.lastUpdatedAt('usage')).toBeNull();
    });

    it('says "last updated" only while stale', () => {
      // markFresh stamps Date.now(), so the baseline has to come from the same
      // clock rather than an arbitrary literal.
      const baseline = Date.now();
      service.markFresh('usage');
      expect(service.relativeLastUpdated('usage', baseline)!.startsWith('last updated')).toBe(
        false,
      );

      service.markStale('usage');
      const note = service.relativeLastUpdated('usage', baseline + 180_000);
      expect(note).toContain('last updated');
      expect(note).toContain('3m ago');
    });

    it('never reports a negative age if the supplied clock is behind the stamp', () => {
      service.markFresh('usage');
      const age = service.relativeLastUpdated('usage', 0)!;
      expect(age).toContain('0s ago');
      expect(age).not.toContain('-');
    });

    it('renders nothing rather than a bare label when there is no timestamp yet', () => {
      expect(service.relativeLastUpdated('health')).toBeNull();
    });
  });

  describe('hasAnyNotice', () => {
    it('is false on a clean session', () => {
      expect(service.hasAnyNotice()).toBe(false);
    });

    it('is true for any visible notice', () => {
      service.report(gatewayError({ kind: 'budget_exhausted' }), 'banner');
      expect(service.hasAnyNotice()).toBe(true);
    });
  });
});
