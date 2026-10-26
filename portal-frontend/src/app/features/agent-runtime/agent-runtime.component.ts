import { Component, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { timer, switchMap, catchError, EMPTY } from 'rxjs';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

@Component({
  selector: 'app-agent-runtime',
  standalone: true,
  imports: [CommonModule],
  template: `
    <section class="page-head">
      <div>
        <p class="eyebrow">Platform / agent runtime</p>
        <h1>Agent runtime</h1>
        <p class="subhead">Orchestrate agent workflows, inspect runs, and manage execution state.</p>
      </div>
    </section>

    <section class="panel tenant-panel">
      <div class="panel-title">
        <div>
          <p class="eyebrow">Live runs</p>
          <h2>Active executions</h2>
        </div>
      </div>
      <div class="key-row">
        @if (executions().length === 0) {
          <div><strong>No active runs</strong><small>Agent executions will appear here as they are triggered.</small></div>
        } @else {
          @for (run of executions(); track run.run_id) {
            <div class="execution-item">
              <div class="run-info">
                <span class="run-id">{{ run.run_id.slice(0, 8) }}...</span>
                <span class="run-model">{{ run.model }}</span>
                <span class="run-status" [class.status-active]="run.status === 'active'">{{ run.status }}</span>
              </div>
              <div class="run-prompt">{{ run.prompt }}</div>
            </div>
          }
        }
      </div>
    </section>
  `,
  styles: [`
    .execution-item {
      padding: 1rem;
      border-bottom: 1px solid var(--border-color);
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }
    .run-info {
      display: flex;
      gap: 1rem;
      align-items: center;
      font-family: monospace;
    }
    .run-id { font-weight: bold; color: var(--text-muted); }
    .run-model { background: var(--bg-alt); padding: 2px 6px; border-radius: 4px; font-size: 0.8rem; }
    .run-status { font-size: 0.8rem; text-transform: uppercase; }
    .status-active { color: var(--color-success); font-weight: bold; }
    .run-prompt { font-size: 0.9rem; color: var(--text-main); }
  `]
})
export class AgentRuntimeComponent {
  private http = inject(HttpClient);
  executions = signal<any[]>([]);

  constructor() {
    timer(0, 5000)
      .pipe(
        switchMap(() =>
          this.http.get<any[]>('/api/v1/agent/executions').pipe(
            catchError((err) => {
              console.error('Failed to fetch executions', err);
              return EMPTY;
            })
          )
        ),
        takeUntilDestroyed()
      )
      .subscribe((data) => this.executions.set(data));
  }
}
