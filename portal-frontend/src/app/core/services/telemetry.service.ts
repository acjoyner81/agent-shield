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

@Injectable({ providedIn: 'root' })
export class TelemetryService {
  private readonly apiUrl = '/api/v1/telemetry';
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

  constructor(private readonly http: HttpClient) {}

  fetchRecentLogs(): void {
    this.http.get<TelemetryLog[]>(`${this.apiUrl}/logs`).subscribe({
      next: (data) => this.logs.set(data),
      error: () => undefined,
    });
  }
}
