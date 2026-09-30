import { ChangeDetectionStrategy, Component, effect, inject, untracked } from '@angular/core';
import { NotificationService, StaleWidget } from '../../core/errors/error-state.service';
import { AuthService } from '../../core/services/auth.service';

/**
 * The app level host for every gateway notice.
 *
 * Renders into the single always present shell rather than into each feature, so
 * a failure cannot be missed because the user is on the wrong page, and so there
 * is exactly one live region to reason about for screen readers.
 *
 * Accessibility rules, from Spec 0013:
 * - The budget banner is `role="alert"` because it needs action. Everything else
 *   is `role="status"`, announced politely.
 * - Nothing here moves focus. A user typing in a form is not interrupted, which
 *   is the specific behaviour that makes an error layer feel hostile.
 * - The countdown is announced on entry and on clear, never once per second, so
 *   the ticking number is `aria-hidden` and the text is not.
 * - Colour is never the only signal; each state carries a word.
 */
@Component({
  selector: 'app-notification-center',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <!-- Polite region: announced without interrupting whatever the user is doing. -->
    <div class="notice-region" role="status" aria-live="polite">
      @if (notifications.rateLimit(); as rateLimit) {
        <div class="notice notice--warn">
          <span class="notice__label">Throttled</span>
          <span class="notice__body">{{ rateLimit.message }}</span>
          @if (rateLimit.secondsRemaining > 0) {
            <!--
              The ticking number is hidden from assistive tech on purpose: a
              live region that announces every second would talk over the user
              for the whole window. The static sentence below carries the fact.
            -->
            <span class="notice__countdown" aria-hidden="true">
              retrying in {{ rateLimit.secondsRemaining }}s
            </span>
            <span class="visually-hidden">Requests are throttled. Try again shortly.</span>
          }
          <button
            type="button"
            class="notice__dismiss"
            (click)="notifications.reportSuccess()"
            aria-label="Dismiss rate limit notice"
          >
            Dismiss
          </button>
        </div>
      }

      @if (notifications.toast(); as toast) {
        <div class="notice notice--info">
          <span class="notice__label">{{ labelFor(toast) }}</span>
          <span class="notice__body">{{ toast.message }}</span>
          <button
            type="button"
            class="notice__dismiss"
            (click)="notifications.dismissToast()"
            aria-label="Dismiss notice"
          >
            Dismiss
          </button>
        </div>
      }
    </div>

    <!-- Assertive: the budget is gone and the user has to act on it. -->
    @if (notifications.budgetBanner(); as budget) {
      <div class="notice notice--danger" role="alert">
        <span class="notice__label">Budget exhausted</span>
        <span class="notice__body">{{ budget.message }}</span>
        <button
          type="button"
          class="notice__dismiss"
          (click)="notifications.dismissBudgetBanner()"
          aria-label="Dismiss budget notice"
        >
          Dismiss
        </button>
      </div>
    }
  `,
  styles: [
    `
      .notice-region {
        position: fixed;
        top: 1rem;
        right: 1rem;
        z-index: 1000;
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
        max-width: 28rem;
      }

      .notice {
        display: flex;
        align-items: baseline;
        gap: 0.5rem;
        padding: 0.75rem 0.9rem;
        border-radius: 8px;
        border: 1px solid #d4d4d8;
        background: #fff;
        box-shadow: 0 8px 24px rgb(0 0 0 / 12%);
        font-size: 0.875rem;
        line-height: 1.4;
      }

      .notice--danger {
        position: fixed;
        top: 1rem;
        left: 50%;
        transform: translateX(-50%);
        z-index: 1001;
        border-color: #dc2626;
      }

      .notice--warn {
        border-color: #d97706;
      }

      .notice--info {
        border-color: #2563eb;
      }

      /* The word, not the colour, is what carries the state. */
      .notice__label {
        font-weight: 600;
        white-space: nowrap;
      }

      .notice__body {
        flex: 1;
      }

      .notice__countdown {
        font-variant-numeric: tabular-nums;
        color: #52525b;
        white-space: nowrap;
      }

      .notice__dismiss {
        border: 1px solid currentColor;
        background: transparent;
        border-radius: 6px;
        padding: 0.15rem 0.5rem;
        font: inherit;
        cursor: pointer;
      }

      .visually-hidden {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip: rect(0 0 0 0);
        white-space: nowrap;
      }
    `,
  ],
})
export class NotificationCenter {
  protected readonly notifications = inject(NotificationService);
  // The app's own auth facade, not the Auth0 SDK directly, so this component
  // depends on one abstraction and the shell's existing test double covers it.
  private readonly auth = inject(AuthService);

  constructor() {
    // The 401 consequence, and the only place in the app that acts on it.
    //
    // `ErrorStateService.report` sets `sessionExpired` for a 401 and exempts that
    // kind from silent suppression, but it is a pure signal store: it holds state
    // and never navigates. Nothing else read this signal, so a dead session
    // produced neither a redirect nor an explanation, and the specs missed it
    // because they assert the signal rather than the navigation it implies.
    //
    // Keyed on the signal, so it fires once per transition to true.
    //
    // The flag is deliberately NOT acknowledged here. Resetting it before the
    // navigation completes re-arms the guard while the app is still on its way
    // out, so a second 401 from a concurrent poll starts a second redirect: a
    // storm of authorization round trips. It is acknowledged below instead, once
    // a live session is confirmed.
    //
    // Only 401 redirects. A 403 must not re-authenticate: the session is valid
    // and simply lacks the scope, so sending the user through Auth0 again lands
    // them back here with the same refusal, which is the loop Spec 0013 forbids.
    effect(() => {
      if (!this.notifications.sessionExpired()) {
        return;
      }
      this.auth.login();
    });

    // Re-arm the guard, but only against a session that is actually live again.
    //
    // Acknowledging on the transition into an authenticated state means a genuine
    // second expiry still redirects, while the repeat 401s of a single expiry do
    // not: they all land while the flag is already true and the effect above has
    // nothing to react to.
    effect(() => {
      if (!this.auth.authenticated()) {
        return;
      }
      untracked(() => this.notifications.acknowledgeSessionExpiry());
    });
  }

  /**
   * A word for every kind, so the state never depends on the colour alone.
   * `guardrail_blocked` and `scope_denied` say which rule or which scope, which
   * is the difference between "something failed" and an actionable message.
   */
  protected labelFor(error: { kind: string; message: string }): string {
    switch (error.kind) {
      case 'scope_denied':
        return 'Permission denied';
      case 'guardrail_blocked':
        return 'Blocked by policy';
      default:
        return 'Gateway error';
    }
  }

  /** Exposed for the shell's health line and for tests. */
  widgetIsStale(widget: StaleWidget): boolean {
    return this.notifications.isStale(widget);
  }
}
