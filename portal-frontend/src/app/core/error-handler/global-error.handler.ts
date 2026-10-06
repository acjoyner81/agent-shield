import { ErrorHandler, Injectable, Injector } from '@angular/core';
import { ErrorStateService } from '../core/errors/error-state.service';
import { GatewayError } from '../core/errors/gateway-error';

@Injectable({ providedIn: 'root' })
export class GlobalErrorHandler extends ErrorHandler {
  constructor(private injector: Injector) {
    super();
  }

  handleError(error: Error | unknown): void {
    // Always log to console first
    console.error('Global error handler:', error);

    // Classify the error
    const classifiedError: GatewayError = {
      kind: 'unexpected',
      message: error instanceof Error ? error.message : String(error),
      timestamp: new Date().toISOString(),
    };

    // Report via ErrorStateService to surface it in the UI
    const errorStateService = this.injector.get(ErrorStateService);
    errorStateService.report(classifiedError, 'global', 'unexpected');
  }
}
