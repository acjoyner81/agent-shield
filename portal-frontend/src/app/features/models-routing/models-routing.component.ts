import { Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { NotificationService } from '../../core/errors/error-state.service';

interface Model {
  model_id: string;
  display_name: string;
  provider_id: string;
  input_cost_per_1k: number;
  output_cost_per_1k: number;
}

@Component({
  selector: 'app-models-routing',
  standalone: true,
  imports: [CommonModule],
  template: `
    <section class="page-head">
      <div>
        <p class="eyebrow">Control plane / routing</p>
        <h1 class="pricing-title">Model Routing</h1>
        <p class="pricing-subtitle">Define which LLM serves as the default for your tenant operations.</p>
      </div>
    </section>

    <div class="dashboard-grid">
      <article class="panel">
        <div class="panel-title">
          <div>
            <p class="eyebrow">Default Model</p>
            <h2>Active Routing</h2>
          </div>
        </div>
        <div class="model-selection-area">
          <div class="current-model-card">
            <div class="model-info">
              <span class="label">Current Default</span>
              <strong class="value">{{ currentModel()?.display_name || 'None selected' }}</strong>
            </div>
            <div class="model-badge">{{ currentModel()?.model_id || 'unassigned' }}</div>
          </div>
        </div>
      </article>

      <article class="panel">
        <div class="panel-title">
          <div>
            <p class="eyebrow">Available Models</p>
            <h2>Model Registry</h2>
          </div>
        </div>
        <div class="model-list">
          @for (model of models(); track model.model_id) {
            <div class="model-row" [class.active]="model.model_id === currentModel()?.model_id">
              <div class="model-main">
                <div class="model-name">
                  <strong>{{ model.display_name }}</strong>
                  <small>{{ model.model_id }}</small>
                </div>
                <div class="model-provider">{{ model.provider_id }}</div>
              </div>
              <div class="model-costs">
                <span>In: \${{ model.input_cost_per_1k }}</span>
                <span>Out: \${{ model.output_cost_per_1k }}</span>
              </div>
              <button 
                class="button" 
                [class.button-primary]="model.model_id === currentModel()?.model_id"
                [class.button-secondary]="model.model_id !== currentModel()?.model_id"
                (click)="setDefault(model.model_id)"
              >
                {{ model.model_id === currentModel()?.model_id ? 'Active' : 'Set Default' }}
              </button>
            </div>
          } @empty {
            <p class="empty-note">No models available in the registry.</p>
          }
        </div>
      </article>
    </div>
  `,
  styles: [`
    .model-selection-area { padding: 20px 0; display: flex; justify-content: center; }
    .current-model-card { 
      background: var(--canvas); 
      border: 1px solid var(--line); 
      border-radius: 12px; 
      padding: 24px; 
      text-align: center; 
      min-width: 300px;
      box-shadow: inset 0 2px 4px rgba(0,0,0,0.05);
    }
    .model-info { display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; }
    .model-info .label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
    .model-info .value { font-size: 24px; font-weight: 800; color: var(--ink); }
    .model-badge { 
      display: inline-block; 
      padding: 4px 12px; 
      background: var(--navy); 
      color: #fff; 
      font-family: 'DM Mono', monospace; 
      font-size: 10px; 
      border-radius: 20px; 
    }
    .model-list { display: flex; flex-direction: column; gap: 12px; margin-top: 16px; }
    .model-row { 
      display: flex; 
      align-items: center; 
      justify-content: space-between; 
      padding: 12px 16px; 
      background: #fff; 
      border: 1px solid var(--line); 
      border-radius: 8px; 
      transition: all 0.2s ease;
    }
    .model-row.active { border-color: var(--teal); box-shadow: 0 0 0 1px var(--teal); background: #f0faf9; }
    .model-main { display: flex; align-items: center; gap: 20px; flex: 1; }
    .model-name { display: flex; flex-direction: column; }
    .model-name strong { font-size: 13px; color: var(--ink); }
    .model-name small { font-size: 11px; color: var(--muted); font-family: 'DM Mono', monospace; }
    .model-provider { 
      font-size: 10px; 
      text-transform: uppercase; 
      color: var(--muted); 
      background: #eee; 
      padding: 2px 6px; 
      border-radius: 4px; 
      font-weight: 600;
    }
    .model-costs { display: flex; gap: 12px; margin-right: 20px; font-size: 11px; color: var(--muted); font-family: 'DM Mono', monospace; }
  `]
})
export class ModelsRoutingComponent {
  private readonly http = inject(HttpClient);
  private readonly notifications = inject(NotificationService);
  
  models = signal<Model[]>([]);
  currentModel = signal<Model | null>(null);
  loading = signal(false);

  ngOnInit() {
    this.loadModels();
    this.loadCurrentDefault();
  }

  loadModels() {
    this.http.get<Model[]>('/api/v1/models').subscribe({
      next: (data: Model[]) => this.models.set(data),
      error: () => this.notifications.report({ kind: 'unexpected', message: 'Failed to load model registry', status: 500 }, 'toast')
    });
  }

  loadCurrentDefault() {
    this.http.get<any>('/api/v1/models/default').subscribe({
      next: (res: any) => {
        const modelId = res.default_model;
        const model = this.models().find(m => m.model_id === modelId);
        this.currentModel.set(model || null);
      },
      error: () => this.notifications.report({ kind: 'unexpected', message: 'Failed to load current default model', status: 500 }, 'toast')
    });
  }

  setDefault(modelId: string) {
    this.loading.set(true);
    this.http.post('/api/v1/models/default', { model: modelId }).subscribe({
      next: () => {
        const model = this.models().find(m => m.model_id === modelId);
        this.currentModel.set(model || null);
        this.notifications.report({ kind: 'unexpected', message: 'Default model updated successfully', status: 200 }, 'toast');
      },
      error: (err: any) => this.notifications.report({ kind: 'unexpected', message: err.error?.detail || 'Failed to set default model', status: err.status || 500 }, 'toast'),
      complete: () => this.loading.set(false)
    });
  }
}