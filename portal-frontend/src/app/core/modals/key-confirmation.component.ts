import { Component, Input, Output, EventEmitter, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-key-confirmation',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="modal-backdrop" (click)="onBackdropClick()">
      <div class="modal-content" (click)="$event.stopPropagation()">
        <div class="modal-header">
          <h3>{{ title }}</h3>
          <button class="modal-close" (click)="onClose()">&times;</button>
        </div>
        <div class="modal-body">
          <p>{{ message }}</p>
        </div>
        <div class="modal-footer">
          <button class="btn-secondary" (click)="onCancel()">{{ cancelText }}</button>
          <button class="btn-primary" (click)="onConfirm()">{{ confirmText }}</button>
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
    .modal-footer {
      display: flex;
      justify-content: flex-end;
      padding: 12px 20px;
      border-top: 1px solid #e5e7eb;
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
      margin-left: 8px;
      cursor: pointer;
      font-size: 0.875rem;
    }
  `]
})
export class KeyConfirmationComponent {
  @Input() title = 'Confirm action';
  @Input() message = 'Are you sure you want to perform this action?';
  @Input() cancelText = 'Cancel';
  @Input() confirmText = 'Confirm';
  
  @Output() confirm = new EventEmitter<void>();
  @Output() cancel = new EventEmitter<void>();
  
  onConfirm() {
    this.confirm.emit();
  }
  
  onCancel() {
    this.cancel.emit();
  }

  onClose() {
    this.onCancel();
  }
  
  onBackdropClick() {
    this.onCancel();
  }
}
