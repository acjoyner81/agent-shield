import { Component, OnInit, inject } from '@angular/core';
import { AgGridAngular } from 'ag-grid-angular';
import type { ColDef } from 'ag-grid-community';
import { themeQuartz } from 'ag-grid-community';
import { computed } from '@angular/core';
import {
  TelemetryLog,
  TelemetryService,
} from '../../core/services/telemetry.service';
import { NotificationService } from '../../core/errors/error-state.service';

@Component({
  selector: 'app-logs',
  standalone: true,
  imports: [AgGridAngular],
  template: `    <section class="page-head">
      <div>
        <p class="eyebrow">Observability / audit stream</p>
        <h1>Real-time observability</h1>
        <p class="subhead">
          Inspect gateway events, evaluation outcomes, and trace context across
          every tenant.
        </p>
        @if (lastUpdated(); as note) {
          <p class="stale-note">{{ note }}</p>
        }
      </div>
    </section>
    <!--
      Every figure below is computed from the fetched rows. "Events today",
      "Avg. latency", and the Download CSV button were hardcoded; the button also
      had no handler, so it was a control that did nothing (Spec 0013).
    -->
    <section class="log-summary">
      <div>
        <span>Events in view</span>
        <strong>{{ telemetry.logs().length }}</strong>
      </div>
      <div>
        <span>Rate limit drops</span
        ><strong class="danger">{{ telemetry.failedRequests() }}</strong>
      </div>
      <div><span>Avg. latency</span><strong>{{ averageLatencyMs() }}</strong></div>
      <div>
        <span>Eval pass rate</span
        ><strong class="success">{{ passRateLabel() }}</strong>
      </div>
    </section>
    <section class="panel grid-panel">
      <div class="grid-toolbar">
        <div class="toolbar-search">
          ⌕ <input placeholder="Filter events, tenants, traces..." />
        </div>
        <div class="toolbar-meta">
          <span class="live-pill"><b></b> Polling every 60s</span
          ><button class="select-button">All tenants ⌄</button>
        </div>
      </div>
      @if (telemetry.logsLoaded() && telemetry.logs().length === 0) {
        <p class="empty-state">
          No telemetry for this tenant yet. Events appear here once a request has
          been metered.
        </p>
      }
      <ag-grid-angular
        class="ag-theme-quartz-dark"
        [theme]="gridTheme"
        [rowData]="telemetry.logs()"
        [columnDefs]="columnDefs"
        [defaultColDef]="defaultColDef"
        [pagination]="true"
        [paginationPageSize]="10"
      ></ag-grid-angular>
    </section>`,
})
export class LogsComponent implements OnInit {
  readonly telemetry = inject(TelemetryService);
  private readonly notifications = inject(NotificationService);

  /**
   * The quiet staleness note, or null when the data is current.
   *
   * A failed poll is deliberately not an interruption: the poller runs every 60
   * seconds, so an interrupting policy would nag a user who did nothing.
   */
  readonly lastUpdated = computed(() => this.notifications.relativeLastUpdated('logs'));

  /**
   * Honest about having no rows, rather than rendering a fabricated 184ms.
   *
   * Only request events carry a latency. Key rotations and token usage have
   * none, so they are excluded from the average instead of being counted as
   * 0ms, which would have dragged the figure down toward the fastest thing the
   * gateway does.
   */
  readonly averageLatencyMs = computed(() => {
    const measured = this.telemetry
      .logs()
      .map((entry) => entry.latencyMs)
      .filter((value): value is number => typeof value === 'number');
    if (measured.length === 0) {
      return '—';
    }
    const total = measured.reduce((sum, value) => sum + value, 0);
    return `${Math.round(total / measured.length)}ms`;
  });

  /** The old template appended a literal ".4%" to a rounded integer. */
  readonly passRateLabel = computed(() => {
    if (this.telemetry.logs().length === 0) {
      return '—';
    }
    return `${this.telemetry.passRate()}%`;
  });
  readonly gridTheme = themeQuartz.withParams({
    backgroundColor: '#111820',
    foregroundColor: '#d6e0e8',
    headerBackgroundColor: '#17212b',
    borderColor: '#293744',
    rowHoverColor: '#192833',
  });
  readonly defaultColDef: ColDef = {
    sortable: true,
    filter: true,
    resizable: true,
  };
  readonly columnDefs: ColDef<TelemetryLog>[] = [
    { field: 'eventId', headerName: 'Event ID', width: 130 },
    { field: 'timestamp', headerName: 'Timestamp', width: 180 },
    { field: 'tenantId', headerName: 'Tenant', width: 120 },
    { field: 'message', headerName: 'Event', flex: 2 },
    {
      field: 'statusCode',
      headerName: 'Status',
      width: 100,
      // A row with no status is not a successful request, it is an event that
      // was never an HTTP response, so it is not coloured as a green 200.
      cellClass: (params) =>
        params.value == null ? 'status-none' : params.value === 429 || params.value >= 500
          ? 'status-danger'
          : 'status-ok',
      valueFormatter: (params) => (params.value == null ? '—' : `${params.value}`),
    },
    {
      field: 'costUsd',
      headerName: 'Cost',
      width: 105,
      valueFormatter: (params) =>
        params.value == null ? '—' : `$${Number(params.value).toFixed(4)}`,
    },
    {
      field: 'latencyMs',
      headerName: 'Latency',
      width: 105,
      valueFormatter: (params) => (params.value == null ? '—' : `${params.value}ms`),
    },
    { field: 'evalPassed', headerName: 'Judge', width: 95 },
    { field: 'traceId', headerName: 'Dynatrace trace', flex: 1 },
  ];
  ngOnInit(): void {
    this.telemetry.fetchRecentLogs();
  }
}
