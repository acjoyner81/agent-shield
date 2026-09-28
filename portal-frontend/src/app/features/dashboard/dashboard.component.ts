import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { CommonModule, DecimalPipe } from '@angular/common';
import { TelemetryService } from '../../core/services/telemetry.service';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [RouterLink, CommonModule, DecimalPipe],
  template: `
    <section class="page-head">
      <div>
        <p class="eyebrow">Control plane / overview</p>
        <h1>Good morning, Anthony.</h1>
        <p class="subhead">Your AI operations are steady. Here is the signal that matters today.</p>
      </div>
      <div class="head-actions">
        <button class="button button-secondary" type="button" (click)="telemetry.refreshDashboard()">
          Refresh <span>↻</span>
        </button>
        <button class="button button-primary" type="button">Export report <span>↗</span></button>
      </div>
    </section>

    <section class="metric-grid">
      <article class="metric-card">
        <div class="metric-label">Total tokens processed</div>
        <div class="metric-value">{{ telemetry.usage()?.totals?.total_tokens | number:'1.0-0' }}</div>
        <div class="metric-foot positive">↑ Active period <span>{{ telemetry.usage()?.period_start }} to {{ telemetry.usage()?.period_end }}</span></div>
        <div class="sparkline teal"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>
      </article>

      <article class="metric-card">
        <div class="metric-label">Estimated spend</div>
        @if (telemetry.canSeeCost()) {
          <div class="metric-value">
            <span class="unit">$</span>{{ telemetry.estimatedCostUsd() | number:'1.2-4' }}
          </div>
          <div class="metric-foot positive">↑ Rate card priced <span>Read at query time</span></div>
        } @else {
          <div class="metric-value">—</div>
          <div class="metric-foot">Spend hidden <span>billing:admin required</span></div>
        }
        <div class="sparkline yellow"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>
      </article>

      <article class="metric-card">
        <div class="metric-label">Quality pass rate</div>
        <div class="metric-value">{{ telemetry.periodPassRate() }}%</div>
        <div class="metric-foot positive">↑ Period quality <span>{{ telemetry.usage()?.totals?.quality_passed | number:'1.0-0' }} eval passes</span></div>
        <div class="sparkline green"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>
      </article>

      <article class="metric-card">
        <div class="metric-label">Failed / Rate-limited</div>
        <div class="metric-value">{{ telemetry.periodFailures() | number:'1.0-0' }} / {{ telemetry.periodRateLimited() | number:'1.0-0' }}</div>
        <div class="metric-foot warning">Perimeter guard <span>Active monitoring</span></div>
        <div class="sparkline coral"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>
      </article>
    </section>

    <section class="dashboard-grid">
      <article class="panel spend-panel">
        <div class="panel-title">
          <div>
            <p class="eyebrow">Financial pulse</p>
            <h2>Token usage by model</h2>
          </div>
          <button class="select-button">{{ telemetry.usage()?.period_start || 'Current' }} <span>⌄</span></button>
        </div>
        <div class="chart">
          <div class="y-axis"><span>100k</span><span>75k</span><span>50k</span><span>25k</span><span>0</span></div>
          <div class="chart-area">
            <div class="grid-line"></div>
            <div class="grid-line"></div>
            <div class="grid-line"></div>
            <div class="grid-line"></div>
            <div class="area-fill"></div>
            <div class="chart-line"></div>
            <div class="x-axis">
              @for (modelSummary of telemetry.usage()?.by_model; track modelSummary.model) {
                <span>{{ modelSummary.model }}</span>
              }
              @empty {
                <span>No active models</span>
              }
            </div>
          </div>
        </div>
      </article>

      <article class="panel activity-panel">
        <div class="panel-title">
          <div>
            <p class="eyebrow">Live signal</p>
            <h2>System health</h2>
          </div>
          <span class="live-pill"><b></b> Live</span>
        </div>
        <div class="health-list">
          @for (service of telemetry.health()?.services ?? []; track service.name) {
            <div>
              <span class="health-name">
                <b class="dot" [class.green-dot]="service.status === 'healthy'" [class.coral-dot]="service.status !== 'healthy'"></b>
                {{ service.name }}
              </span>
              <strong>{{ service.status }}</strong>
              <small>{{ service.latency_ms | number:'1.0-0' }}ms</small>
            </div>
          } @empty {
            <div>
              <span class="health-name">No service probes reported yet</span>
            </div>
          }
        </div>
        <div class="health-footer" [class.degraded-text]="telemetry.health()?.overall !== 'healthy'">
          {{ telemetry.health()?.overall === 'healthy' ? 'All systems operational' : 'Degraded: one or more services need attention' }}
          <span>↗</span>
        </div>
      </article>
    </section>

    <section class="panel tenant-panel">
      <div class="panel-title">
        <div>
          <p class="eyebrow">Portfolio view</p>
          <h2>Model Breakdown</h2>
        </div>
        <a routerLink="/logs" class="text-link">View all activity →</a>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th>Requests</th>
              <th>Input Tokens</th>
              <th>Output Tokens</th>
              <th>Total Tokens</th>
              @if (telemetry.canSeeCost()) {
                <th>Cost</th>
              }
              <th></th>
            </tr>
          </thead>
          <tbody>
            @for (item of telemetry.usage()?.by_model; track item.model) {
              <tr>
                <td><strong>{{ item.model }}</strong><small>Active deployment</small></td>
                <td>{{ item.request_count | number:'1.0-0' }}</td>
                <td>{{ item.input_tokens | number:'1.0-0' }}</td>
                <td>{{ item.output_tokens | number:'1.0-0' }}</td>
                <td><strong>{{ item.total_tokens | number:'1.0-0' }}</strong></td>
                @if (telemetry.canSeeCost()) {
                  <td>{{ item.cost_usd | number:'1.2-4' }}</td>
                }
                <td>•••</td>
              </tr>
            } @empty {
              <tr>
                <td [attr.colspan]="telemetry.canSeeCost() ? 7 : 6" class="text-center">No telemetry usage data recorded for this period yet.</td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    </section>
  `,
})
export class DashboardComponent implements OnInit, OnDestroy {
  readonly telemetry = inject(TelemetryService);

  ngOnInit(): void {
    this.telemetry.fetchRecentLogs();
    this.telemetry.refreshDashboard();
    this.telemetry.startAutoRefresh();
  }

  ngOnDestroy(): void {
    this.telemetry.stopAutoRefresh();
  }
}
