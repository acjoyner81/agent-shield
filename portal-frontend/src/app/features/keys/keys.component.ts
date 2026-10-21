import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { KeysService, APIKey } from '../../core/services/keys.service';
import { AuthService } from '../../core/services/auth.service';
import { KeyConfirmationComponent } from '../../core/modals/key-confirmation.component';
import { KeyCreateComponent } from '../../core/modals/key-create.component';
import { KeyScopeEditorComponent } from '../../core/modals/key-scope-editor.component';
import { NotificationService } from '../../core/errors/error-state.service';
import { ENFORCED_SCOPES, describeScopes } from '../../core/scope-catalog';

@Component({
  selector: 'app-keys',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    KeyConfirmationComponent,
    KeyCreateComponent,
    KeyScopeEditorComponent,
  ],
  template: `
    <section class="page-head">
      <div>
        <p class="eyebrow">Security / access</p>
        <h1>API keys & access</h1>
        <p class="subhead">Control tenant credentials, role permissions, and the guardrails around every request.</p>
      </div>
      <button class="button button-primary create-key-trigger" type="button" (click)="openCreateModal()">+ Create API key</button>
    </section>

    @if (newlyCreatedSecret()) {
      <div class="panel alert-panel" style="background: #111827; border: 1px solid #10b981; margin-bottom: 24px; padding: 16px; border-radius: 8px;">
        <p class="eyebrow" style="color: #10b981;">New API Key Generated</p>
        <p style="color: #fff; margin: 8px 0;">Please copy your secret key now. You won't be able to see it again!</p>
        <code style="background: #1f2937; padding: 8px 12px; display: block; color: #34d399; border-radius: 4px; word-break: break-all;">{{ newlyCreatedSecret() }}</code>
        <button class="button" type="button" style="margin-top: 12px; background: #374151; color: #fff;" (click)="newlyCreatedSecret.set(null)">Close</button>
      </div>
    }

    <section class="security-grid">
      <article class="panel security-card">
        <p class="eyebrow">Your access</p>
        <h2>Signed-in principal</h2>
        <div class="principal-row">
          <div>
            <b class="avatar violet">{{ initials() }}</b>
            <span>{{ auth.userName() }}</span>
            <small>{{ tenantName() }}</small>
          </div>
        </div>
        <p class="small-note" style="margin: 4px 0 12px;">
          Scopes are carried by your token and checked per route. They are not roles
          you can edit here; the gateway reads them from Auth0's app metadata.
        </p>
        @if (auth.permissions().length > 0) {
          <ul class="scope-inventory">
            @for (scope of authScopes(); track scope.scope) {
              <li>
                <b class="dot green-dot"></b>
                <span><b>{{ scope.scope }}</b><small>{{ scope.effect }}</small></span>
              </li>
            }
          </ul>
        } @else {
          <p class="empty-note" role="alert">
            Your token carries no enforced scopes, so every permission-gated route
            will answer 403. Ask an administrator to add scopes to your account.
          </p>
        }
      </article>

      <article class="panel security-card">
        <p class="eyebrow">Protection status</p>
        <h2>Guardrails active</h2>
        <div class="guardrail"><span><b class="dot green-dot"></b>Prompt injection filter</span><strong>Enabled</strong></div>
        <div class="guardrail"><span><b class="dot yellow-dot"></b>PII redaction</span><strong>Not applied</strong></div>
        <div class="guardrail"><span><b class="dot green-dot"></b>Budget enforcement</span><strong>Enabled</strong></div>
        <div class="guardrail"><span><b class="dot yellow-dot"></b>OIDC enforcement</span><strong>Review</strong></div>
        <p class="small-note" style="margin: 12px 0 0;">
          PII redaction is <b>not applied to request or response traffic</b>. The gateway
          ships the helpers but the request path never calls them, so prompts reach the
          model as sent. Do not send personal data until this row reads Enabled.
        </p>
      </article>
    </section>

    <section class="panel tenant-panel">
      <div class="panel-title">
        <div>
          <p class="eyebrow">Credentials</p>
          <h2>Active API keys</h2>
        </div>
        <span class="small-note">
          Rotate keys every 90 days
          @if (keysNote(); as note) {
            <em class="stale-note"> · {{ note }}</em>
          }
        </span>
      </div>
      @for (key of keysService.keys(); track key.key_id) {
        <div class="key-row">
          <div class="key-identity">
            <strong>{{ key.name }}</strong>
            <small>{{ key.key_prefix }} · Created {{ key.created_at }}</small>
            <small class="key-scopes">Scopes: {{ describeScopes(key.permissions) }}</small>
          </div>
          <span class="key-status">{{ key.status }}</span>
          <button class="text-link" type="button" (click)="openScopeModal(key)">Manage</button>
          <button class="icon-button" type="button" title="Revoke Key" (click)="openRevokeModal(key.key_id)">🗑</button>
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

    @if (isCreateModalOpen()) {
      <app-key-create (create)="onCreateKey($event)" (cancel)="closeCreateModal()" />
    }

    @if (isRevokeModalOpen()) {
      <app-key-confirmation
        title="Revoke API Key"
        message="Are you sure you want to revoke this API key? This action cannot be undone."
        (confirm)="onRevokeKey()"
        (cancel)="closeRevokeModal()"
      />
    }

    @if (scopeTarget(); as key) {
      <app-key-scope-editor
        [keyName]="key.name"
        [currentScopes]="key.permissions ?? []"
        [grantableScopes]="auth.permissions()"
        (save)="onSaveScopes($event)"
        (cancel)="closeScopeModal()"
      />
    }
  `,
})
export class KeysComponent implements OnInit {
  readonly keysService = inject(KeysService);
  readonly auth = inject(AuthService);
  readonly newlyCreatedSecret = signal<string | null>(null);
  private readonly notifications = inject(NotificationService);

  /**
   * A failed key list load keeps the keys on screen and marks them old, so the
   * list has to say so too. A revoked key still shown next to "Rotate keys
   * every 90 days" reads as live, and a stale list on a credentials page is a
   * security question, not a cosmetic one.
   */
  readonly keysNote = computed(() => this.notifications.staleNote('keys'));

  readonly gatewayUrl = `${window.location.origin}/api`;
  readonly docsUrl = `${window.location.origin}/api/docs`;
  readonly describeScopes = describeScopes;

  /**
   * The signed-in principal's own scopes, labelled with what each one opens.
   * This is what the page used to fake with three invented people holding
   * invented roles: access state this gateway actually stores is per key, and
   * the only other real scope set is the one on the token in hand.
   */
  readonly authScopes = computed(() => {
    const held = new Set(this.auth.permissions());
    return ENFORCED_SCOPES.filter((definition) => held.has(definition.scope));
  });

  readonly initials = computed(() => {
    const name = this.auth.userName().trim();
    const parts = name.split(/\s+/).filter(Boolean);
    if (parts.length === 0) {
      return '?';
    }
    const first = parts[0][0];
    const last = parts.length > 1 ? parts[parts.length - 1][0] : '';
    return (first + last).toUpperCase();
  });

  readonly tenantName = this.auth.tenantName;

  // Modal state
  readonly isCreateModalOpen = signal<boolean>(false);
  readonly isRevokeModalOpen = signal<boolean>(false);
  readonly revokeKeyId = signal<string>('');
  readonly scopeTarget = signal<APIKey | null>(null);

  ngOnInit(): void {
    this.keysService.fetchKeys();
  }

  openCreateModal(): void {
    this.isCreateModalOpen.set(true);
  }

  closeCreateModal(): void {
    this.isCreateModalOpen.set(false);
  }

  onCreateKey(name: string): void {
    this.keysService.createKey(name).subscribe({
      next: (res) => {
        if (res.secret_key) {
          this.newlyCreatedSecret.set(res.secret_key);
        }
        this.keysService.fetchKeys();
        this.closeCreateModal();
      },
    });
  }

  openRevokeModal(keyId: string): void {
    this.revokeKeyId.set(keyId);
    this.isRevokeModalOpen.set(true);
  }

  closeRevokeModal(): void {
    this.isRevokeModalOpen.set(false);
    this.revokeKeyId.set('');
  }

  onRevokeKey(): void {
    this.keysService.revokeKey(this.revokeKeyId()).subscribe({
      next: () => this.keysService.fetchKeys(),
    });
    this.closeRevokeModal();
  }

  openScopeModal(key: APIKey): void {
    this.scopeTarget.set(key);
  }

  closeScopeModal(): void {
    this.scopeTarget.set(null);
  }

  onSaveScopes(permissions: string[]): void {
    const key = this.scopeTarget();
    if (!key) {
      return;
    }
    this.keysService.updateKeyScopes(key.key_id, permissions).subscribe({
      next: () => this.keysService.fetchKeys(),
    });
    this.closeScopeModal();
  }
}
