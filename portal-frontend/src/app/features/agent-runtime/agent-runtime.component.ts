import { Component } from '@angular/core';

@Component({
  selector: 'app-agent-runtime',
  standalone: true,
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
        <div><strong>No active runs</strong><small>Agent executions will appear here as they are triggered.</small></div>
      </div>
    </section>
  `,
})
export class AgentRuntimeComponent {}
