import { Component, Input, Output, EventEmitter, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

@Component({
  selector: 'app-key-create',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
      <div class="modal-backdrop" (click)="onBackdropClick()">
      <div class="modal-content" role="dialog" aria-modal="true" aria-labelledby="create-key-title" (click)="$event.stopPropagation()">
        <div class="modal-header">
          <h3 id="create-key-title">Create API Key</h3>
          <button class="modal-close" type="button" aria-label="Close create API key dialog" (click)="onClose()">&times;</button>
        </div>
        <div class="modal-body">
          <label class="form-label" for="key-name">Key name</label>
          <input 
            id="key-name" 
            type="text" 
            class="form-input" 
            [(ngModel)]="keyName"
            placeholder="e.g., Staging Gateway"
            required
          />
        </div>
        <div class="modal-footer">
          <button class="btn-secondary" type="button" (click)="onCancel()">Cancel</button>
          <button class="btn-primary" type="button" (click)="onCreate()">Create</button>
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
      width: min(400px, 100%);
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
    .modal-body {
      padding: 24px;
      color: #374151;
    }
    .modal-footer {
      display: flex;
      justify-content: flex-end;
      flex-wrap: wrap;
      gap: 8px;
      padding: 0 24px 20px;
    }
    .form-label {
      display: block;
      margin-bottom: 8px;
      font-size: 0.875rem;
      font-weight: 500;
      color: #374151;
    }
    .form-input {
      width: 100%;
      box-sizing: border-box;
      padding: 8px 12px;
      border: 1px solid #d1d5db;
      border-radius: 4px;
      font-size: 1rem;
      margin-top: 4px;
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
    @media (max-width: 480px) {
      .modal-backdrop { padding: 12px; }
      .modal-content { max-height: calc(100vh - 24px); max-height: calc(100dvh - 24px); }
      .modal-header { padding: 14px 18px; }
      .modal-body { padding: 18px; }
      .modal-footer { padding: 0 18px 18px; }
    }
    @media (max-width: 340px) {
      .modal-footer { flex-direction: column-reverse; }
      .modal-footer button { width: 100%; }
    }
  `]
})
export class KeyCreateComponent {
  keyName = signal<string>('');
  
  @Output() create = new EventEmitter<string>();
  @Output() cancel = new EventEmitter<void>();
  
  onCreate() {
    if (this.keyName().trim()) {
      this.create.emit(this.keyName());
    }
    this.onCancel();
  }
  
  onCancel() {
    this.keyName.set('');
    this.cancel.emit();
  }
  
  onBackdropClick() {
    this.onCancel();
  }

  onClose() {
    this.onCancel();
  }
}
