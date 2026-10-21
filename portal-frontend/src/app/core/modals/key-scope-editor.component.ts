import { Component, Input, Output, EventEmitter, computed, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ENFORCED_SCOPES } from '../scope-catalog';

/**
 * Edit the scope allowlist of one API key.
 *
 * Only scopes the caller holds are offered. The gateway refuses a grant the
 * caller cannot make, so listing an unheld scope would be a checkbox whose only
 * possible outcome is a 403; a control that cannot succeed is worse than an
 * absent one, because it reads as a capability the user has.
 */
@Component({
  selector: 'app-key-scope-editor',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="modal-backdrop" (click)="onCancel()">
      <div
        class="modal-content"
        role="dialog"
        aria-modal="true"
        aria-labelledby="key-scope-title"
        (click)="$event.stopPropagation()"
      >
        <div class="modal-header">
          <h3 id="key-scope-title">Manage access</h3>
          <button class="modal-close" type="button" aria-label="Close manage access dialog" (click)="onCancel()">&times;</button>
        </div>
        <div class="modal-body">
          <p class="key-name">{{ keyName }}</p>

          @if (selectable().length === 0) {
            <p class="empty" role="alert">
              Your principal holds no grantable scopes, so there is nothing this key
              can be granted. Ask an administrator to add scopes to your account.
            </p>
          } @else {
            <ul class="scope-list">
              @for (scope of selectable(); track scope.scope) {
                <li>
                  <label>
                    <input
                      type="checkbox"
                      [checked]="selected().has(scope.scope)"
                      (change)="toggle(scope.scope)"
                    />
                    <span>
                      <b>{{ scope.scope }}</b>
                      <small>{{ scope.effect }}</small>
                    </span>
                  </label>
                </li>
              }
            </ul>
          }

          @if (held().length > 0) {
            <p class="small-note">
              This key also holds {{ held().join(', ') }}, which your principal cannot
              grant or revoke. Those scopes are left untouched.
            </p>
          }
        </div>
        <div class="modal-footer">
          <button class="btn-secondary" type="button" (click)="onCancel()">Cancel</button>
          <button class="btn-primary" type="button" [disabled]="!canSave()" (click)="onSave()">Save scopes</button>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .modal-backdrop {
      position: fixed;
      inset: 0;
      padding: 16px;
      background: rgba(0, 0, 0, 0.5);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1000;
      overflow-y: auto;
    }
    .modal-content {
      background: white;
      margin: auto;
      border-radius: 8px;
      width: min(460px, 100%);
      max-height: calc(100vh - 32px);
      max-height: calc(100dvh - 32px);
      overflow-y: auto;
      animation: modalIn 0.3s ease-out;
    }
    @keyframes modalIn { from { opacity: 0; transform: scale(0.9); } to { opacity: 1; transform: scale(1); } }
    .modal-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 16px 20px;
      border-bottom: 1px solid #e5e7eb;
    }
    .modal-close {
      background: none;
      border: none;
      font-size: 1.5rem;
      cursor: pointer;
      color: #6b7280;
    }
    .modal-body { padding: 20px 24px; color: #374151; }
    .key-name { font-weight: 600; margin: 0 0 14px; }
    .empty { color: #92400e; font-size: 0.875rem; margin: 0; }
    .scope-list { list-style: none; margin: 0; padding: 0; }
    .scope-list li { border-top: 1px solid #edf1f2; }
    .scope-list label {
      display: flex;
      gap: 10px;
      align-items: flex-start;
      padding: 11px 0;
      cursor: pointer;
    }
    .scope-list b { display: block; font-size: 0.8125rem; }
    .scope-list small { display: block; color: #92a0a6; font-size: 0.75rem; margin-top: 3px; }
    .small-note { color: #94a1a7; font-size: 0.75rem; margin: 14px 0 0; }
    .modal-footer {
      display: flex;
      justify-content: flex-end;
      flex-wrap: wrap;
      gap: 8px;
      padding: 0 24px 20px;
    }
    .btn-secondary {
      background: #f3f4f6;
      border: none;
      padding: 8px 16px;
      border-radius: 4px;
      cursor: pointer;
      font-size: 0.875rem;
    }
    .btn-primary {
      background: #10b981;
      color: white;
      border: none;
      padding: 8px 16px;
      border-radius: 4px;
      cursor: pointer;
      font-size: 0.875rem;
    }
    .btn-primary[disabled] { opacity: 0.5; cursor: not-allowed; }
    @media (max-width: 480px) {
      .modal-backdrop { padding: 12px; }
      .modal-content { max-height: calc(100vh - 24px); max-height: calc(100dvh - 24px); }
      .modal-header { padding: 14px 18px; }
      .modal-body { padding: 16px 18px; }
      .modal-footer { padding: 0 18px 18px; }
    }
  `],
})
export class KeyScopeEditorComponent {
  @Input() keyName = 'API key';
  /** The scopes the key holds today. */
  @Input({ required: true }) set currentScopes(value: string[]) {
    this._initial.set([...(value ?? [])]);
    this.selected.set(new Set(value ?? []));
  }
  /** The scopes the signed-in principal is allowed to grant. */
  @Input({ required: true }) set grantableScopes(value: string[]) {
    this._grantable.set([...(value ?? [])]);
  }

  @Output() save = new EventEmitter<string[]>();
  @Output() cancel = new EventEmitter<void>();

  private readonly _grantable = signal<string[]>([]);
  private readonly _initial = signal<string[]>([]);
  readonly selected = signal<Set<string>>(new Set());

  /**
   * The scopes this principal may actually change on this key: the intersection
   * of what it holds and what the catalog names. A key that holds a scope the
   * principal lacks keeps it, and that scope is reported rather than silently
   * dropped, because saving the rest of the form must not strip it.
   */
  readonly selectable = computed(() => {
    const held = new Set(this._grantable());
    return ENFORCED_SCOPES.filter((definition) => held.has(definition.scope));
  });

  /** Scopes on the key that this principal cannot grant or revoke. */
  readonly held = computed(() => {
    const grantable = new Set(this._grantable());
    return this._initial().filter((scope) => !grantable.has(scope)).sort();
  });

  /** The full allowlist to persist: the untouched remainder plus the selection. */
  readonly canSave = computed(() => this.selectable().length > 0);

  toggle(scope: string): void {
    const next = new Set(this.selected());
    if (next.has(scope)) {
      next.delete(scope);
    } else {
      next.add(scope);
    }
    this.selected.set(next);
  }

  onSave(): void {
    if (!this.canSave()) {
      return;
    }
    const unchanged = this.held();
    const chosen = this.selectable()
      .map((definition) => definition.scope)
      .filter((scope) => this.selected().has(scope));
    this.save.emit([...unchanged, ...chosen]);
  }

  onCancel(): void {
    this.cancel.emit();
  }
}
