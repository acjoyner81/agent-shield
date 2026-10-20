import { Component } from '@angular/core';

@Component({
  selector: 'app-models-routing',
  standalone: true,
  template: `
    <section class="page-head">
      <div>
        <p class="eyebrow">Platform / models & routing</p>
        <h1>Models & routing</h1>
        <p class="subhead">Manage LLM models, providers, routing policies, and fallbacks.</p>
      </div>
    </section>

    <section class="panel tenant-panel">
      <div class="panel-title">
        <div>
          <p class="eyebrow">Model configuration</p>
          <h2>Active models</h2>
        </div>
      </div>
      <div class="key-row">
        <div><strong>No models configured</strong><small>Model and routing configuration will appear here.</small></div>
      </div>
    </section>
  `,
})
export class ModelsRoutingComponent {}
