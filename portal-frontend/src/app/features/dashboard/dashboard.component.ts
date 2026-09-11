import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { TelemetryService } from '../../core/services/telemetry.service';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [RouterLink],
  template: `
    <section class="page-head"><div><p class="eyebrow">Control plane / overview</p><h1>Good morning, Anthony.</h1><p class="subhead">Your AI operations are steady. Here is the signal that matters today.</p></div><button class="button button-primary" type="button">Export report <span>↗</span></button></section>
    <section class="metric-grid">
      <article class="metric-card"><div class="metric-label">Monthly spend</div><div class="metric-value">$2,840.62</div><div class="metric-foot positive">↓ 12.4% <span>vs last month</span></div><div class="sparkline teal"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div></article>
      <article class="metric-card"><div class="metric-label">Requests processed</div><div class="metric-value">1.28M</div><div class="metric-foot positive">↑ 8.7% <span>vs last month</span></div><div class="sparkline yellow"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div></article>
      <article class="metric-card"><div class="metric-label">Quality pass rate</div><div class="metric-value">{{ telemetry.passRate() }}.4%</div><div class="metric-foot positive">↑ 2.1% <span>LLM judge average</span></div><div class="sparkline green"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div></article>
      <article class="metric-card"><div class="metric-label">Active rate limit</div><div class="metric-value">73<span class="unit">%</span></div><div class="metric-foot warning">Peak at 09:42 <span>2 fallback events</span></div><div class="sparkline coral"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div></article>
    </section>
    <section class="dashboard-grid">
      <article class="panel spend-panel"><div class="panel-title"><div><p class="eyebrow">Financial pulse</p><h2>Spend by tenant</h2></div><button class="select-button">Last 30 days <span>⌄</span></button></div><div class="chart"><div class="y-axis"><span>$4k</span><span>$3k</span><span>$2k</span><span>$1k</span><span>$0</span></div><div class="chart-area"><div class="grid-line"></div><div class="grid-line"></div><div class="grid-line"></div><div class="grid-line"></div><div class="area-fill"></div><div class="chart-line"></div><div class="x-axis"><span>May 10</span><span>May 17</span><span>May 24</span><span>May 31</span><span>Jun 07</span></div></div></div></article>
      <article class="panel activity-panel"><div class="panel-title"><div><p class="eyebrow">Live signal</p><h2>System health</h2></div><span class="live-pill"><b></b> Live</span></div><div class="health-list"><div><span class="health-name"><b class="dot green-dot"></b>FastAPI Gateway</span><strong>99.98%</strong><small>42ms</small></div><div><span class="health-name"><b class="dot green-dot"></b>Java MCP Server</span><strong>99.99%</strong><small>18ms</small></div><div><span class="health-name"><b class="dot yellow-dot"></b>Primary LLM</span><strong>98.21%</strong><small>1.4s</small></div><div><span class="health-name"><b class="dot green-dot"></b>Redis Cache</span><strong>100%</strong><small>2ms</small></div></div><div class="health-footer">All systems operational <span>↗</span></div></article>
    </section>
    <section class="panel tenant-panel"><div class="panel-title"><div><p class="eyebrow">Portfolio view</p><h2>Tenant activity</h2></div><a routerLink="/logs" class="text-link">View all activity →</a></div><div class="table-wrap"><table><thead><tr><th>Tenant</th><th>Requests</th><th>Spend</th><th>Quality</th><th>Usage</th><th></th></tr></thead><tbody><tr><td><strong>Northstar Labs</strong><small>northstar-labs</small></td><td>482,109</td><td>$1,204.32</td><td><span class="quality">98.4%</span></td><td><div class="usage-bar"><i style="width: 78%"></i></div></td><td>•••</td></tr><tr><td><strong>Harbor Works</strong><small>harbor-works</small></td><td>301,844</td><td>$892.18</td><td><span class="quality">96.1%</span></td><td><div class="usage-bar"><i style="width: 58%"></i></div></td><td>•••</td></tr><tr><td><strong>Pinnacle Care</strong><small>pinnacle-care</small></td><td>198,220</td><td>$504.06</td><td><span class="quality caution">89.7%</span></td><td><div class="usage-bar"><i class="caution-bar" style="width: 42%"></i></div></td><td>•••</td></tr></tbody></table></div></section>
  `,
})
export class DashboardComponent { readonly telemetry = inject(TelemetryService); }
