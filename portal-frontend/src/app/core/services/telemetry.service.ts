import { Injectable, computed, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';

import { SILENT_POLL } from '../errors/surface.context';

export interface TelemetryLog {
  eventId: string;
  timestamp: string;
  tenantId: string;
  message: string;
  /** Null when the event was never an HTTP response, e.g. a key rotation. */
  costUsd: number | null;
  /** Null when the event was never an HTTP response, e.g. a token usage row. */
  statusCode: number | null;
  /** Null when the event carries no measured latency, e.g. a key rotation. */
  latencyMs: number | null;
  evalPassed: boolean | null;
  traceId: string;
}

export interface UsageSummary {
  tenant_id: string;
  period_start: string;
  period_end: string;
  totals: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    total_requests: number;
    quality_passed: number;
    failed_requests: number;
    rate_limited_requests: number;
    estimated_cost_usd: number | null;
  };
  by_model: Array<{
    model: string;
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    request_count: number;
    cost_usd: number | null;
  }>;
}

export interface ServiceHealth {
  name: string;
  status: 'healthy' | 'degraded';
  latency_ms: number;
}

export interface ServiceHealthResponse {
  services: ServiceHealth[];
  overall: 'healthy' | 'degraded';
}

/** Dashboard poll cadence (Spec 0011 AC-5). */
export const AUTO_REFRESH_INTERVAL_MS = 60000;

@Injectable({ providedIn: 'root' })
export class TelemetryService {
  private readonly apiUrl = '/api/v1/telemetry';
  private refreshHandle: ReturnType<typeof setInterval> | null = null;

  readonly usage = signal<UsageSummary | null>(null);
  readonly health = signal<ServiceHealthResponse | null>(null);
  /**
   * Starts empty.
   *
   * This used to be seeded with five hardcoded `EVT-*` rows so the page had
   * something to render. Because every fetch failure kept the previous values, a
   * tenant with the gateway down saw a complete, plausible, entirely fictional
   * audit stream. An observability product asserting events that never happened
   * is worse than one admitting it has none, so the empty state is the fix.
   */
  readonly logs = signal<TelemetryLog[]>([]);

  /** True once a fetch has succeeded at least once, so empty can mean either thing. */
  readonly logsLoaded = signal(false);

  /** Only rows that actually carry a cost contribute; a key rotation costs nothing. */
  readonly totalSpend = computed(() =>
    this.logs().reduce((sum, log) => sum + (log.costUsd ?? 0), 0),
  );
  readonly failedRequests = computed(
    () =>
      this.logs().filter(
        (log) => log.statusCode === 429 || (log.statusCode != null && log.statusCode >= 500),
      ).length,
  );
  readonly passRate = computed(() => {
    const entries = this.logs();
    return entries.length ? Math.round((entries.filter((log) => log.evalPassed).length / entries.length) * 100) : 0;
  });

  /** Period spend, null unless the principal holds billing:admin (Spec 0011 AC-2, AC-6). */
  readonly estimatedCostUsd = computed(() => this.usage()?.totals?.estimated_cost_usd ?? null);
  readonly canSeeCost = computed(() => this.estimatedCostUsd() !== null);

  /** Quality pass rate over the period, derived from the eval flagged request count. */
  readonly periodPassRate = computed(() => {
    const totals = this.usage()?.totals;
    if (!totals || !totals.total_requests) {
      return 0;
    }
    return Math.round((totals.quality_passed / totals.total_requests) * 100);
  });

  readonly periodFailures = computed(() => this.usage()?.totals?.failed_requests ?? 0);
  readonly periodRateLimited = computed(() => this.usage()?.totals?.rate_limited_requests ?? 0);

  constructor(private readonly http: HttpClient) {}

  /**
   * All three are background polls. Every 60 seconds the dashboard refetches
   * them, so a policy that interrupted would emit a notice roughly every 30
   * seconds to a user who did nothing. A failure marks the widget stale and
   * keeps the last known figures, with a quiet "last updated N ago" note.
   */
  fetchRecentLogs(): void {
    this.http.get<TelemetryLog[]>(`${this.apiUrl}/logs`, { context: SILENT_POLL }).subscribe({
      next: (data) => {
        this.logs.set(data);
        this.logsLoaded.set(true);
      },
      // Already classified and reported by the interceptor; see keys.service.ts
      // for why the handler is present but empty.
      error: () => undefined,
    });
  }

  fetchUsageSummary(): void {
    this.http.get<UsageSummary>('/api/v1/usage/summary', { context: SILENT_POLL }).subscribe({
      next: (data) => this.usage.set(data),
      error: () => undefined,
    });
  }

  fetchServiceHealth(): void {
    this.http
      .get<ServiceHealthResponse>('/api/v1/health/services', { context: SILENT_POLL })
      .subscribe({
        next: (data) => this.health.set(data),
        error: () => undefined,
      });
  }

  /** Manual refresh: pull both dashboard signals now (Spec 0011 AC-5). */
  refreshDashboard(): void {
    this.fetchUsageSummary();
    this.fetchServiceHealth();
  }

  startAutoRefresh(intervalMs: number = AUTO_REFRESH_INTERVAL_MS): void {
    this.stopAutoRefresh();
    this.refreshHandle = setInterval(() => this.refreshDashboard(), intervalMs);
  }

  stopAutoRefresh(): void {
    if (this.refreshHandle !== null) {
      clearInterval(this.refreshHandle);
      this.refreshHandle = null;
    }
  }
}
