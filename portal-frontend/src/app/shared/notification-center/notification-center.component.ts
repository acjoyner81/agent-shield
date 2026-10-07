import { ChangeDetectionStrategy, Component, effect, inject, untracked } from '@angular/core';
import { NotificationService } from '../../core/errors/error-state.service';
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
    <div class="notice-region" role="status" aria-live="polite">
      <!--
        A session the gateway will not accept. It lives inside the polite region
        so it stacks with the rest instead of landing on top of the budget
        banner, which claims the top centre, and it stays until a request
        succeeds because the automatic redirect is only ever spent once. A user
        left with no explanation and no way forward is worse off than one who is
        told.

        role="alert" on the notice itself: it is the one state that needs action
        now, and the deadline is the tenant's work, not the session's.
      -->
      @if (notifications.sessionNotice(); as session) {
        <div class="notice notice--session" role="alert">
          <span class="notice__label">Session expired</span>
          <span class="notice__body">{{ session.message }}</span>
          <button
            type="button"
            class="notice__dismiss"
            (click)="signInAgain()"
            aria-label="Sign in again"
          >
            Sign in again
          </button>
        </div>
      }

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
            (click)="notifications.dismissRateLimit()"
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

    <!-- Assertive and on its own: the budget is gone and the user has to act. -->
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

      /* Same alarm colour as the budget, without the fixed placement: this one
         stacks inside the region so the two never overlap. */
      .notice--session {
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

  /**
   * Latch: has this component already sent a redirect for the current expiry?
   *
   * Instance state, not signal state, on purpose. A signal would be read by the
   * effect that writes it, which is exactly the cycle that pegged the renderer.
   * Plain field, so no effect can depend on it.
   */
  private redirectSent = false;

  /** Gateway successes at the moment the redirect went out, so we can spot the next one. */
  private successesAtRedirect = 0;

  constructor() {
    // The 401 consequence, and the only place in the app that acts on it.
    //
    // `NotificationService.report` sets `sessionExpired` for a 401 and exempts that
    // kind from silent suppression, but it is a pure signal store: it holds state
    // and never navigates. Nothing else read this signal, so a dead session
    // produced neither a redirect nor an explanation, and the specs missed it
    // because they assert the signal rather than the navigation it implies.
    //
    // Only 401 redirects. A 403 must not re-authenticate: the session is valid
    // and simply lacks the scope, so sending the user through Auth0 again lands
    // them back here with the same refusal, which is the loop Spec 0013 forbids.
    //
    // The flag is acknowledged BEFORE navigating, not after, so a 401 arriving
    // between the acknowledge and the navigation completing cannot start a
    // second redirect. `redirectSent` covers the remaining case: two 401s in the
    // same tick, which the acknowledge alone cannot serialise.
    effect(() => {
      if (!this.notifications.sessionExpired() || this.redirectSent) {
        return;
      }
      this.redirectSent = true;
      this.successesAtRedirect = this.notifications.acceptedResponses();
      // Inside untracked so the write does not re-trigger this effect; the flag
      // is cleared in the same tick the redirect is issued.
      untracked(() => this.notifications.acknowledgeSessionExpiry());
      this.auth.login();
    });

    // Re-arm the latch only on proof that the gateway accepted the session.
    //
    // This used to read `auth.authenticated`, which is the Auth0 SDK's own
    // session flag. That flag answers "do I hold a structurally valid token",
    // not "will the gateway accept it", so a token minted with the wrong
    // audience, a revoked user, or any other audience or issuer mismatch looked
    // exactly like a recovered session: the latch dropped, the next 401
    // redirected, the rejected token was replaced by another rejected token.
    // Measured in the browser at 261 token exchanges and 1018 refused calls in
    // about eight seconds, hammering Auth0's token endpoint.
    //
    // A 2xx from the gateway is the only evidence that answers the question that
    // actually matters, and it is already counted for exactly this purpose. The
    // cost is that a session which expires again while no request succeeds gets
    // no second automatic redirect: the notice stays up instead, with a sign in
    // control the user can press, which cannot become a storm.
    effect(() => {
      const accepted = this.notifications.acceptedResponses();
      if (!this.redirectSent || accepted === this.successesAtRedirect) {
        return;
      }
      untracked(() => {
        this.redirectSent = false;
      });
    });
  }

  /**
   * A user pressed retry, which is not a loop: somebody has to be present to
   * press it. The latch stays as it is, so this cannot compound with the 401s
   * arriving in the meantime.
   */
  protected signInAgain(): void {
    this.auth.login();
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
}
