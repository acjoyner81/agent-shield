import { Component, Input, Output, EventEmitter, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

@Component({
  selector: 'app-key-create',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="modal-backdrop" (click)="onBackdropClick()">
      <div class="modal-content" (click)="$event.stopPropagation()">
        <div class="modal-header">
          <h3>Create API Key</h3>
          <button class="modal-close" (click)="onClose()">&times;</button>
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
          <button class="btn-secondary" (click)="onCancel()">Cancel</button>
          <button class="btn-primary" (click)="onCreate()">Create</button>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .modal-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.5);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1000;
    }
    .modal-content {
      background: white;
      margin: 2rem;
      border-radius: 8px;
      width: 400px;
      max-width: 90vw;
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
    .form-label {
      display: block;
      margin-bottom: 8px;
      font-size: 0.875rem;
      font-weight: 500;
      color: #374151;
    }
    .form-input {
      width: 100%;
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
      margin-right: 8px;
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
