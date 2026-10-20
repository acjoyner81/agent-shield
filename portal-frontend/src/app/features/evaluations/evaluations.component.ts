import { Component } from '@angular/core';

@Component({
  selector: 'app-evaluations',
  standalone: true,
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
        <div><strong>No evaluations recorded</strong><small>Evaluation results will appear here once runs are configured.</small></div>
      </div>
    </section>
  `,
})
export class EvaluationsComponent {}
