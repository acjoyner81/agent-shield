import { Injectable, computed, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';

export interface TelemetryLog {
  eventId: string;
  timestamp: string;
  tenantId: string;
  costUsd: number;
  statusCode: number;
  latencyMs: number;
  evalPassed: boolean;
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
  readonly logs = signal<TelemetryLog[]>([
    { eventId: 'EVT-1030', timestamp: 'Today, 10:42:18', tenantId: 'northstar-labs', costUsd: 0.0038, statusCode: 429, latencyMs: 12, evalPassed: true, traceId: 'dt-7fa2c1' },
    { eventId: 'EVT-1029', timestamp: 'Today, 10:41:56', tenantId: 'harbor-works', costUsd: 0.0182, statusCode: 200, latencyMs: 142, evalPassed: true, traceId: 'dt-7fa2af' },
    { eventId: 'EVT-1028', timestamp: 'Today, 10:41:11', tenantId: 'northstar-labs', costUsd: 0.0114, statusCode: 200, latencyMs: 86, evalPassed: true, traceId: 'dt-7fa1d9' },
    { eventId: 'EVT-1027', timestamp: 'Today, 10:40:44', tenantId: 'pinnacle-care', costUsd: 0.0061, statusCode: 500, latencyMs: 934, evalPassed: false, traceId: 'dt-7fa19b' },
    { eventId: 'EVT-1026', timestamp: 'Today, 10:39:58', tenantId: 'harbor-works', costUsd: 0.0027, statusCode: 200, latencyMs: 64, evalPassed: true, traceId: 'dt-7fa0e0' },
  ]);

  readonly totalSpend = computed(() => this.logs().reduce((sum, log) => sum + log.costUsd, 0));
  readonly failedRequests = computed(() => this.logs().filter((log) => log.statusCode === 429 || log.statusCode >= 500).length);
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

  fetchRecentLogs(): void {
    this.http.get<TelemetryLog[]>(`${this.apiUrl}/logs`).subscribe({
      next: (data) => this.logs.set(data),
      error: (err) => console.warn('Failed to fetch telemetry logs', err),
    });
  }

  fetchUsageSummary(): void {
    this.http.get<UsageSummary>('/api/v1/usage/summary').subscribe({
      next: (data) => this.usage.set(data),
      error: (err) => console.warn('Failed to fetch usage summary', err),
    });
  }

  fetchServiceHealth(): void {
    this.http.get<ServiceHealthResponse>('/api/v1/health/services').subscribe({
      next: (data) => this.health.set(data),
      error: (err) => console.warn('Failed to fetch service health', err),
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
