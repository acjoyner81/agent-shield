import { Injectable, OnDestroy, computed, signal } from '@angular/core';
import { GatewayError } from '../errors/gateway-error';

/**
 * Every piece of gateway failure state the portal shows, in one place.
 *
 * No component holds error state. A banner reads a signal here and nothing
 * else, so a 429 from the dashboard poller and a 429 from a clicked button
 * coalesce into the same entry instead of stacking two banners.
 *
 * Signals rather than a store library: the portal already derives state this
 * way in `TelemetryService`, and there is no cross cutting library to justify.
 */

export type StaleWidget = 'usage' | 'health' | 'logs' | 'keys';

/** A live countdown, so the banner can render "retrying in 9s" without polling. */
export interface RateLimitState {
  message: string;
  /** Seconds left, recomputed on every 429. */
  secondsRemaining: number;
  /** From `X-RateLimit-Remaining`, when the gateway sent it. */
  remaining?: number;
}

export interface WidgetStaleness {
  /** Epoch ms of the last successful fetch. Null until the first success. */
  lastUpdatedAt: number | null;
  stale: boolean;
}

const TOAST_LIFETIME_MS = 6000;
/** Ticks the rate limit countdown. A second is the resolution Retry-After gives. */
const COUNTDOWN_TICK_MS = 1000;

@Injectable({ providedIn: 'root' })
export class NotificationService implements OnDestroy {
  private toastTimer: ReturnType<typeof setTimeout> | null = null;
  private countdownTimer: ReturnType<typeof setInterval> | null = null;

  /**
   * The 402 banner, or null.
   *
   * Deliberately not keyed by tenant: the portal has no tenant identity source,
   * and a session is a single tenant, so a key would be unavailable exactly when
   * it is needed.
   */
  readonly budgetBanner = signal<GatewayError | null>(null);

  /** The sticky 429 entry, or null. A second 429 updates it, never stacks. */
  readonly rateLimit = signal<RateLimitState | null>(null);

  readonly toast = signal<GatewayError | null>(null);

  /** One-shot trigger the container turns into exactly one redirect. */
  readonly sessionExpired = signal(false);

  /**
   * The visible half of a session expiry, or null.
   *
   * Separate from the trigger above because the two have different lifetimes.
   * The trigger is spent by the redirect, which happens once. The notice has to
   * survive until something proves the session works again, otherwise a user
   * whose re-minted token the gateway still refuses is left with no explanation
   * at all: every request 401s, no banner says why, and the bounded redirect
   * looks like the app having decided to ignore them.
   */
  readonly sessionNotice = signal<GatewayError | null>(null);

  /**
   * Counts gateway 2xx responses, which is the only evidence available that the
   * gateway accepts the session the portal is holding.
   *
   * It exists because the Auth0 SDK reports itself authenticated while holding
   * any structurally valid token, including one the gateway refuses, so its
   * session flag cannot tell a working token from a rejected one. The container
   * needs that distinction to decide whether a second redirect is warranted.
   */
  private readonly gatewaySuccesses = signal(0);
  readonly acceptedResponses = this.gatewaySuccesses.asReadonly();

  private readonly staleness = signal<Record<StaleWidget, WidgetStaleness>>({
    usage: { lastUpdatedAt: null, stale: false },
    health: { lastUpdatedAt: null, stale: false },
    logs: { lastUpdatedAt: null, stale: false },
    keys: { lastUpdatedAt: null, stale: false },
  });

  /** Everything the container renders, derived once. */
  readonly hasAnyNotice = computed(
    () =>
      this.budgetBanner() !== null ||
      this.rateLimit() !== null ||
      this.toast() !== null ||
      this.sessionNotice() !== null,
  );

  /** Per widget. Reads keep the last known figures; they only add a quiet note. */
  isStale(widget: StaleWidget): boolean {
    return this.staleness()[widget].stale;
  }

  lastUpdatedAt(widget: StaleWidget): number | null {
    return this.staleness()[widget].lastUpdatedAt;
  }

  /**
   * The one entry point the interceptor calls.
   *
   * `policy` is what the call site declared, never what was inferred here. A
   * `silent` failure marks its widget stale and raises nothing else.
   */
  report(
    error: GatewayError,
    policy: 'silent' | 'banner' | 'toast',
    widget?: StaleWidget,
  ): void {
    // A session expiry overrides a silent policy. The dashboard's 60s poll is
    // often the only signal that the session died, and no user action is coming
    // to produce another request, so suppressing it would strand the user on a
    // dead session with no explanation.
    if (policy === 'silent' && error.kind !== 'session_expired') {
      if (widget !== undefined) {
        this.markStale(widget);
      }
      return;
    }

    switch (error.kind) {
      case 'budget_exhausted':
        this.budgetBanner.set(error);
        return;
      case 'rate_limited':
        this.setRateLimit(error);
        return;
      case 'session_expired':
        this.sessionExpired.set(true);
        this.sessionNotice.set(error);
        return;
      case 'scope_denied':
      case 'guardrail_blocked':
      case 'unexpected':
        this.showToast(error);
        return;
    }
  }

  /**
   * Any 2xx clears the sticky rate limit state.
   *
   * Waiting out the full `Retry-After` instead would leave a banner claiming the
   * tenant is throttled while their requests already succeed, which is the kind
   * of false statement this layer exists to stop.
   *
   * It is also the only proof that the gateway accepts the current session, so
   * it clears the session notice and is what lets the container spend another
   * redirect on a later, genuinely new expiry.
   */
  reportSuccess(): void {
    this.gatewaySuccesses.update((count) => count + 1);
    this.sessionNotice.set(null);
    this.clearRateLimit();
  }

  dismissBudgetBanner(): void {
    // Per banner, not a mute: the next 402 sets it again, so a tenant cannot
    // dismiss the explanation once and keep retrying an action that stays
    // refused.
    this.budgetBanner.set(null);
  }

  dismissToast(): void {
    this.clearToastTimer();
    this.toast.set(null);
  }

  /**
   * The user saying "I have read this", which is not the same claim as a
   * successful response. Kept separate from `reportSuccess` so a dismiss click
   * cannot pass for gateway evidence that the session works.
   */
  dismissRateLimit(): void {
    this.clearRateLimit();
  }

  acknowledgeSessionExpiry(): void {
    this.sessionExpired.set(false);
  }

  markStale(widget: StaleWidget): void {
    this.staleness.update((current) => ({
      ...current,
      [widget]: { ...current[widget], stale: true },
    }));
  }

  markFresh(widget: StaleWidget): void {
    this.staleness.update((current) => ({
      ...current,
      [widget]: { lastUpdatedAt: Date.now(), stale: false },
    }));
  }

  /** "last updated 3m ago". Display formatting, so it lives here, not the classifier. */
  relativeLastUpdated(widget: StaleWidget, now: number = Date.now()): string | null {
    const { lastUpdatedAt, stale } = this.staleness()[widget];
    if (lastUpdatedAt === null) {
      return null;
    }
    const seconds = Math.max(0, Math.round((now - lastUpdatedAt) / 1000));
    const label = seconds < 60 ? `${seconds}s ago` : `${Math.round(seconds / 60)}m ago`;
    return stale ? `last updated ${label}` : label;
  }

  /**
   * The note only while the widget is stale.
   *
   * For chrome that should stay quiet when the data is fresh. A poll running
   * every 60 seconds would otherwise rewrite "3s ago" on the page twice a minute
   * to tell the user nothing, and the note exists to say the figures on screen
   * are old, not to report the passing of time.
   */
  staleNote(widget: StaleWidget): string | null {
    return this.isStale(widget) ? this.relativeLastUpdated(widget) : null;
  }

  private setRateLimit(error: GatewayError): void {
    this.rateLimit.set({
      message: error.message,
      // Absent Retry-After means 0, so nothing ticks and the banner waits for a
      // success. That is the documented fallback, not a guessed duration.
      secondsRemaining: error.retryAfterSeconds ?? 0,
      remaining: error.remaining,
    });
    if (error.retryAfterSeconds !== undefined && error.retryAfterSeconds > 0) {
      this.startCountdown();
    }
  }

  private startCountdown(): void {
    this.stopCountdown();
    this.countdownTimer = setInterval(() => {
      const current = this.rateLimit();
      if (current === null) {
        this.stopCountdown();
        return;
      }
      const next = current.secondsRemaining - 1;
      if (next <= 0) {
        this.rateLimit.set({ ...current, secondsRemaining: 0 });
        this.stopCountdown();
        return;
      }
      this.rateLimit.set({ ...current, secondsRemaining: next });
    }, COUNTDOWN_TICK_MS);
  }

  private clearRateLimit(): void {
    this.stopCountdown();
    this.rateLimit.set(null);
  }

  private stopCountdown(): void {
    if (this.countdownTimer !== null) {
      clearInterval(this.countdownTimer);
      this.countdownTimer = null;
    }
  }

  private showToast(error: GatewayError): void {
    this.clearToastTimer();
    this.toast.set(error);
    this.toastTimer = setTimeout(() => {
      this.toast.set(null);
      this.toastTimer = null;
    }, TOAST_LIFETIME_MS);
  }

  private clearToastTimer(): void {
    if (this.toastTimer !== null) {
      clearTimeout(this.toastTimer);
      this.toastTimer = null;
    }
  }

  ngOnDestroy(): void {
    this.stopCountdown();
    this.clearToastTimer();
  }
}
