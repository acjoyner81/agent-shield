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

export type StaleWidget = 'usage' | 'health' | 'logs';

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

  /** One-shot guard so a burst of 401s cannot start a redirect storm. */
  readonly sessionExpired = signal(false);

  private readonly staleness = signal<Record<StaleWidget, WidgetStaleness>>({
    usage: { lastUpdatedAt: null, stale: false },
    health: { lastUpdatedAt: null, stale: false },
    logs: { lastUpdatedAt: null, stale: false },
  });

  /** Everything the container renders, derived once. */
  readonly hasAnyNotice = computed(
    () => this.budgetBanner() !== null || this.rateLimit() !== null || this.toast() !== null,
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
   */
  reportSuccess(): void {
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
