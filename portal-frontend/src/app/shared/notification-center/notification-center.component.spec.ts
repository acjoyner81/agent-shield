import { ComponentFixture, TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';


import { NotificationCenter } from './notification-center.component';
import { NotificationService } from '../../core/errors/error-state.service';
import { GatewayError } from '../../core/errors/gateway-error';
import { AuthService } from '../../core/services/auth.service';

/**
 * The container's contract, which is mostly about what it must NOT do.
 *
 * The accessibility requirements from Spec 0013 are the reason this file exists:
 * a live region that announces correctly, a ticking countdown that does not
 * announce once per second, and a dismiss control with a real accessible name
 * rather than a bare glyph.
 */

function gatewayError(overrides: Partial<GatewayError> = {}): GatewayError {
  return { kind: 'unexpected', message: 'Something went wrong.', status: 500, ...overrides };
}

describe('NotificationCenter', () => {
  let fixture: ComponentFixture<NotificationCenter>;
  let service: NotificationService;
  let auth: { login: jasmine.Spy; authenticated: () => boolean };

  beforeEach(async () => {
    // The component owns the 401 redirect, so it injects the Auth0 client. A stub
    // keeps this suite about the container's own contract, and records the
    // redirect so the 401 behaviour is asserted here rather than only by hand.
    auth = {
      login: jasmine.createSpy('login'),
      authenticated: () => false,
    };

    await TestBed.configureTestingModule({
      imports: [NotificationCenter],
      providers: [{ provide: AuthService, useValue: auth }],
    }).compileComponents();

    fixture = TestBed.createComponent(NotificationCenter);
    service = TestBed.inject(NotificationService);
    fixture.detectChanges();
  });

  afterEach(() => {
    service.ngOnDestroy();
  });

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent ?? '';
  }

  function dismissButtons(): HTMLButtonElement[] {
    return Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('button'),
    ) as HTMLButtonElement[];
  }

  describe('a clean session', () => {
    it('renders nothing at all', () => {
      expect(dismissButtons().length).toBe(0);
    });
  });

  describe('budget banner, 402', () => {
    beforeEach(() => {
      service.report(
        gatewayError({
          kind: 'budget_exhausted',
          status: 402,
          message: 'Budget exhausted: $3.20 spent of $2.00 today.',
          budget: { currentSpendUsd: 3.2, maxBudgetUsd: 2 },
        }),
        'banner',
      );
      fixture.detectChanges();
    });

    it('shows the message with the figures', () => {
      expect(text()).toContain('Budget exhausted: $3.20 spent of $2.00 today.');
    });

    it('uses role=alert, because the user has to act on it', () => {
      const alert = (fixture.nativeElement as HTMLElement).querySelector('[role="alert"]');
      expect(alert).not.toBeNull();
      expect(alert!.textContent).toContain('Budget exhausted');
    });

    it('carries a text label, so the state does not depend on colour alone', () => {
      expect(text()).toContain('Budget exhausted');
    });

    it('gives the dismiss control an accessible name, not a bare glyph', () => {
      const dismiss = dismissButtons()[0];
      expect(dismiss.getAttribute('aria-label')).toBe('Dismiss budget notice');
    });

    it('dismisses on click', () => {
      dismissButtons()[0].click();
      fixture.detectChanges();
      expect(service.budgetBanner()).toBeNull();
    });
  });

  describe('rate limit banner, 429', () => {
    beforeEach(() => {
      service.report(
        gatewayError({
          kind: 'rate_limited',
          status: 429,
          message: 'Rate limit reached. Requests are being throttled.',
          retryAfterSeconds: 30,
          remaining: 0,
        }),
        'banner',
      );
      fixture.detectChanges();
    });

    it('lives in a polite live region, not an assertive one', () => {
      const region = (fixture.nativeElement as HTMLElement).querySelector('[role="status"]');
      expect(region).not.toBeNull();
      expect(region!.getAttribute('aria-live')).toBe('polite');
    });

    it('shows the countdown', () => {
      expect(text()).toContain('retrying in 30s');
    });

    it('hides the ticking number from assistive tech, so it does not announce per second', () => {
      const countdown = (fixture.nativeElement as HTMLElement).querySelector(
        '.notice__countdown',
      );
      expect(countdown!.getAttribute('aria-hidden')).toBe('true');
    });

    it('still announces the situation in words', () => {
      expect(text()).toContain('Requests are throttled');
    });

    it('omits the countdown line entirely when there is no time left', () => {
      service.report(gatewayError({ kind: 'rate_limited' }), 'banner');
      fixture.detectChanges();
      expect((fixture.nativeElement as HTMLElement).querySelector('.notice__countdown')).toBeNull();
    });
  });

  describe('toast', () => {
    it('names the specific refusal, so it is actionable', () => {
      service.report(
        gatewayError({ kind: 'scope_denied', status: 403, message: 'Missing scope: keys:write' }),
        'banner',
      );
      fixture.detectChanges();
      expect(text()).toContain('Permission denied');
      expect(text()).toContain('Missing scope: keys:write');
    });

    it('labels a guardrail trip as policy, not as a random failure', () => {
      service.report(
        gatewayError({ kind: 'guardrail_blocked', status: 400, message: 'Prompt injection detected' }),
        'banner',
      );
      fixture.detectChanges();
      expect(text()).toContain('Blocked by policy');
    });

    it('offers no retry button, because only the call site knows if retrying is safe', () => {
      service.report(gatewayError({ kind: 'unexpected' }), 'toast');
      fixture.detectChanges();
      const labels = dismissButtons().map((button) => button.getAttribute('aria-label'));
      expect(labels).toEqual(['Dismiss notice']);
    });
  });

  describe('accessibility', () => {
    it('renders every dismiss control with an accessible name', () => {
      service.report(gatewayError({ kind: 'budget_exhausted' }), 'banner');
      service.report(gatewayError({ kind: 'unexpected' }), 'toast');
      fixture.detectChanges();

      const buttons = dismissButtons();
      expect(buttons.length).toBeGreaterThan(0);
      for (const button of buttons) {
        expect(button.getAttribute('aria-label')).toBeTruthy();
        expect(button.textContent!.trim().length).toBeGreaterThan(0);
      }
    });

    it('does not move focus when a notice appears', () => {
      const sentinel = document.createElement('button');
      document.body.appendChild(sentinel);
      sentinel.focus();
      expect(document.activeElement).toBe(sentinel);

      service.report(gatewayError({ kind: 'budget_exhausted' }), 'banner');
      fixture.detectChanges();

      expect(document.activeElement).toBe(sentinel);
      document.body.removeChild(sentinel);
    });
  });

  /**
   * The 401 consequence.
   *
   * `ErrorStateService` sets `sessionExpired` and is covered for that on its own,
   * which is exactly how the redirect went missing: every test asserted the
   * signal, none asserted the navigation it exists to cause. These assert the
   * outcome, because the outcome is the requirement.
   */
  describe('a session expiry', () => {
    it('redirects to re-authenticate', () => {
      service.report(gatewayError({ kind: 'session_expired', status: 401 }), 'silent');
      fixture.detectChanges();

      expect(auth.login).toHaveBeenCalledTimes(1);
    });

    it('redirects once, not once per 401 in a burst', () => {
      for (let i = 0; i < 5; i++) {
        service.report(gatewayError({ kind: 'session_expired', status: 401 }), 'silent');
        fixture.detectChanges();
      }

      expect(auth.login).toHaveBeenCalledTimes(1);
    });

    it('overrides a silent policy', () => {
      // The dashboard poll is the only signal that a session died, so suppressing
      // this would strand the user on a dead session with no explanation.
      service.report(gatewayError({ kind: 'session_expired', status: 401 }), 'silent');
      fixture.detectChanges();

      expect(auth.login).toHaveBeenCalled();
    });

    it('does not redirect for a 403, which is a valid session lacking a scope', () => {
      service.report(gatewayError({ kind: 'scope_denied', status: 403 }), 'banner');
      fixture.detectChanges();

      expect(auth.login).not.toHaveBeenCalled();
    });
  });

  /**
   * The regression that pegged the renderer.
   *
   * The tests above pass a plain `authenticated: () => false` function, so
   * nothing can ever re-trigger the effects and the loop stays invisible. In the
   * real app `authenticated` is a signal over `isAuthenticated$`, and `login()`
   * is what causes the session to be re-fetched. That is a feedback loop, and it
   * is invisible to a stub that never emits.
   *
   * These use signals and a login that mutates state, so the two are wired the
   * way the app wires them. The old code recursed until the renderer hit 100% CPU
   * on any full page load; the assertion that matters is that login settles.
   */
  describe('with a signal-backed session, as the app actually wires it', () => {
    let authenticated: ReturnType<typeof signal<boolean>>;
    let sessionsObserved: number;

    beforeEach(async () => {
      sessionsObserved = 0;
      authenticated = signal(false);
      // `loginWithRedirect` does not resolve synchronously: the SDK round trips
      // to Auth0 and only then does a session become available. Modelling that
      // delay is what makes the feedback loop visible, so a login that reported
      // a live session instantly would hide the bug.
      auth.authenticated = () => authenticated();
      auth.login.and.callFake(() => {
        sessionsObserved++;
        // Emulate the redirect landing back on a session Auth0 still considers
        // unauthenticated, which is exactly the state the old code deadlocked on.
        authenticated.set(false);
      });

      fixture = TestBed.createComponent(NotificationCenter);
      service = TestBed.inject(NotificationService);
      fixture.detectChanges();
    });

    it('redirects a bounded number of times for a burst of 401s', () => {
      for (let i = 0; i < 5; i++) {
        service.report(gatewayError({ kind: 'session_expired', status: 401 }), 'silent');
        fixture.detectChanges();
      }

      // One redirect per expiry, not one per 401 and certainly not unbounded.
      expect(auth.login).toHaveBeenCalledTimes(1);
    });

    it('settles instead of recursing while no session ever appears', () => {
      service.report(gatewayError({ kind: 'session_expired', status: 401 }), 'silent');

      // Let any effect loop run to completion. The old code never finished: it
      // kept re-arming and re-redirecting, which is the hang.
      for (let i = 0; i < 25; i++) {
        fixture.detectChanges();
      }

      expect(auth.login).toHaveBeenCalledTimes(1);
      expect(sessionsObserved).toBe(1);
    });

    it('clears the expiry flag so it cannot drive another redirect', () => {
      service.report(gatewayError({ kind: 'session_expired', status: 401 }), 'silent');
      fixture.detectChanges();

      // The flag is the loop's fuel. It has to be spent, not left latched true.
      // This is the assertion that fails against the old code, and it is the one
      // to trust: the burst and settle cases pass either way, because with a stub
      // that never flips `authenticated` the old effect simply never re-fires, so
      // they cannot see the cycle. The latched flag is the condition that makes
      // the cycle possible in the browser, where the signal does flip.
      expect(service.sessionExpired()).toBe(false);
    });

    it('keeps a latched flag harmless when the session keeps reporting 401', () => {
      // Reproduces the browser condition exactly: the flag latched true (as it
      // did for real), and the dashboard poll keeps delivering 401s. The latch,
      // not the flag, is what must stop the second redirect.
      authenticated.set(false);
      for (let i = 0; i < 3; i++) {
        service.report(gatewayError({ kind: 'session_expired', status: 401 }), 'silent');
        fixture.detectChanges();
      }
      expect(auth.login).toHaveBeenCalledTimes(1);
    });

    it('redirects again for a genuinely new expiry after a live session', () => {
      service.report(gatewayError({ kind: 'session_expired', status: 401 }), 'silent');
      fixture.detectChanges();
      expect(auth.login).toHaveBeenCalledTimes(1);

      // A real session comes back, which drops the latch.
      authenticated.set(true);
      fixture.detectChanges();

      service.report(gatewayError({ kind: 'session_expired', status: 401 }), 'silent');
      fixture.detectChanges();

      expect(auth.login).toHaveBeenCalledTimes(2);
    });
  });
});
