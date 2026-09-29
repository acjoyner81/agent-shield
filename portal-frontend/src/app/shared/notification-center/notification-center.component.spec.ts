import { ComponentFixture, TestBed } from '@angular/core/testing';

import { NotificationCenter } from './notification-center.component';
import { NotificationService } from '../../core/errors/error-state.service';
import { GatewayError } from '../../core/errors/gateway-error';

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

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [NotificationCenter],
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
});
