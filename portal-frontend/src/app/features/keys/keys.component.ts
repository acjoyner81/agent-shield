import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { KeysService, APIKey } from '../../core/services/keys.service';

@Component({
  selector: 'app-keys',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="page-head">
      <div>
        <p class="eyebrow">Security / access</p>
        <h1>API keys & access</h1>
        <p class="subhead">Control tenant credentials, role permissions, and the guardrails around every request.</p>
      </div>
      <button class="button button-primary" type="button" (click)="openCreateModal()">+ Create API key</button>
    </section>

    @if (newlyCreatedSecret()) {
      <div class="panel alert-panel" style="background: #111827; border: 1px solid #10b981; margin-bottom: 24px; padding: 16px; border-radius: 8px;">
        <p class="eyebrow" style="color: #10b981;">New API Key Generated</p>
        <p style="color: #fff; margin: 8px 0;">Please copy your secret key now. You won't be able to see it again!</p>
        <code style="background: #1f2937; padding: 8px 12px; display: block; color: #34d399; border-radius: 4px; word-break: break-all;">{{ newlyCreatedSecret() }}</code>
        <button class="button" style="margin-top: 12px; background: #374151; color: #fff;" (click)="newlyCreatedSecret.set(null)">Close</button>
      </div>
    }

    <section class="security-grid">
      <article class="panel security-card">
        <p class="eyebrow">Workspace roles</p>
        <h2>RBAC policy</h2>
        <div class="role-row"><span><b class="avatar violet">AJ</b><span><strong>Anthony Joyner</strong><small>Owner · Full access</small></span></span><button class="text-link">Manage</button></div>
        <div class="role-row"><span><b class="avatar teal-avatar">MO</b><span><strong>Maria Ortiz</strong><small>Operator · Observability</small></span></span><button class="text-link">Manage</button></div>
        <div class="role-row"><span><b class="avatar yellow-avatar">SC</b><span><strong>Sam Chen</strong><small>Viewer · Read only</small></span></span><button class="text-link">Manage</button></div>
      </article>
      <article class="panel security-card">
        <p class="eyebrow">Protection status</p>
        <h2>Guardrails active</h2>
        <div class="guardrail"><span><b class="dot green-dot"></b>Prompt injection filter</span><strong>Enabled</strong></div>
        <div class="guardrail"><span><b class="dot green-dot"></b>PII redaction</span><strong>Enabled</strong></div>
        <div class="guardrail"><span><b class="dot green-dot"></b>Budget enforcement</span><strong>Enabled</strong></div>
        <div class="guardrail"><span><b class="dot yellow-dot"></b>OIDC enforcement</span><strong>Review</strong></div>
      </article>
    </section>

    <section class="panel tenant-panel">
      <div class="panel-title">
        <div>
          <p class="eyebrow">Credentials</p>
          <h2>Active API keys</h2>
        </div>
        <span class="small-note">Rotate keys every 90 days</span>
      </div>
      @for (key of keysService.keys(); track key.key_id) {
        <div class="key-row">
          <div><strong>{{ key.name }}</strong><small>{{ key.key_prefix }} · Created {{ key.created_at }}</small></div>
          <span class="key-status">{{ key.status }}</span>
          <button class="icon-button" title="Revoke Key" (click)="revokeKey(key.key_id)">🗑</button>
        </div>
      }
      @empty {
        <div class="key-row">
          <div><strong>No active API keys</strong><small>Create a key above to start making programmatic requests.</small></div>
        </div>
      }
    </section>

    <section class="panel tenant-panel">
      <div class="panel-title">
        <div>
          <p class="eyebrow">Developers</p>
          <h2>Quick start</h2>
        </div>
        <a class="text-link" [href]="docsUrl" target="_blank" rel="noopener">Open API reference &rarr;</a>
      </div>
      <p class="small-note" style="margin: 0 0 12px;">
        Send the key as <code>X-Tenant-API-Key</code>. The tenant is always read from the key,
        so a <code>X-Tenant-ID</code> header you set is ignored.
      </p>
      <pre style="background: #0f172a; color: #e2e8f0; padding: 14px 16px; border-radius: 8px; overflow-x: auto; margin: 0;"><code>curl {{ gatewayUrl }}/v1/usage/summary \
  -H "X-Tenant-API-Key: $AGENTSHIELD_API_KEY"</code></pre>
      <p class="small-note" style="margin: 12px 0 0;">
        The key is only shown once, right after you create it. Store it in your secret manager.
      </p>
    </section>
  `,
})
export class KeysComponent implements OnInit {
  readonly keysService = inject(KeysService);
  readonly newlyCreatedSecret = signal<string | null>(null);

  /** The gateway is published on port 8000; /docs and /openapi.json live at its root. */
  readonly gatewayUrl = `${window.location.protocol}//${window.location.hostname}:8000`;
  readonly docsUrl = `${this.gatewayUrl}/docs`;

  ngOnInit(): void {
    this.keysService.fetchKeys();
  }

  openCreateModal(): void {
    const name = prompt('Enter a name for the new API key (e.g., Staging Gateway):', 'Production App Key');
    if (name) {
      this.keysService.createKey(name).subscribe({
        next: (res) => {
          if (res.secret_key) {
            this.newlyCreatedSecret.set(res.secret_key);
          }
          this.keysService.fetchKeys();
        },
      });
    }
  }

  revokeKey(keyId: string): void {
    if (confirm('Are you sure you want to revoke this API key? This action cannot be undone.')) {
      this.keysService.revokeKey(keyId).subscribe({
        next: () => this.keysService.fetchKeys(),
      });
    }
  }
}