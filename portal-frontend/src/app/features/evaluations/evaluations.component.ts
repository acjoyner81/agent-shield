import { Component, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { timer, switchMap, catchError, EMPTY } from 'rxjs';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

@Component({
  selector: 'app-evaluations',
  standalone: true,
  imports: [CommonModule],
  template: `
    <section class="page-head">
      <div>
        <p class="eyebrow">Platform / evaluations</p>
        <h1>Evaluations</h1>
        <p class="subhead">Track quality scores, regression tests, and evaluation runs.</p>
      </div>
    </section>

    <section class="panel tenant-panel">
      <div class="panel-title">
        <div>
          <p class="eyebrow">Quality metrics</p>
          <h2>Evaluation history</h2>
        </div>
      </div>
      <div class="key-row">
        @if (evals().length === 0) {
          <div><strong>No evaluations recorded</strong><small>Evaluation results will appear here once runs are configured.</small></div>
        } @else {
          @for (eval of evals(); track eval.run_id) {
            <div class="eval-item">
              <div class="eval-header">
                <span class="run-id">{{ eval.run_id.slice(0, 8) }}...</span>
                <span class="eval-score" [style.color]="getScoreColor(eval.score)">{{ (eval.score * 100).toFixed(0) }}%</span>
              </div>
              <div class="eval-reason">{{ eval.reason }}</div>
              <div class="eval-time">{{ eval.timestamp | date:'short' }}</div>
            </div>
          }
        }
      </div>
    </section>
  `,
  styles: [`
    .eval-item {
      padding: 1rem;
      border-bottom: 1px solid var(--border-color);
      display: flex;
      flex-direction: column;
      gap: 0.4rem;
    }
    .eval-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .run-id { font-family: monospace; font-weight: bold; color: var(--text-muted); }
    .eval-score { font-weight: bold; font-size: 1.1rem; }
    .eval-reason { font-size: 0.9rem; }
    .eval-time { font-size: 0.8rem; color: var(--text-muted); }
  `]
})
export class EvaluationsComponent {
  private http = inject(HttpClient);
  evals = signal<any[]>([]);

  constructor() {
    timer(0, 5000)
      .pipe(
        switchMap(() =>
          this.http.get<any[]>('/api/v1/evals/recent').pipe(
            catchError((err) => {
              console.error('Failed to fetch evals', err);
              return EMPTY;
            })
          )
        ),
        takeUntilDestroyed()
      )
      .subscribe((data) => this.evals.set(data));
  }

  getScoreColor(score: number): string {
    if (score >= 0.9) return 'var(--color-success)';
    if (score >= 0.7) return 'var(--color-warning)';
    return 'var(--color-danger)';
  }
}
