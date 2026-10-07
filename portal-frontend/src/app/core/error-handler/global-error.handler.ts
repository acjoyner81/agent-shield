import { ErrorHandler, Injectable, Injector, inject } from '@angular/core';
import { NotificationService } from '../errors/error-state.service';
import { GatewayError } from '../errors/gateway-error';

@Injectable({ providedIn: 'root' })
export class GlobalErrorHandler extends ErrorHandler {
  // Angular constructs this through DI (`useClass`), so `inject()` resolves in
  // the constructor's injection context; a constructor parameter would only
  // restate what the injector already carries.
  private readonly injector = inject(Injector);

  override handleError(error: Error | unknown): void {
    // Always log to console first
    console.error('Global error handler:', error);

    // Classify the error
    const classifiedError: GatewayError = {
      kind: 'unexpected',
      message: error instanceof Error ? error.message : String(error),
      status: 0,
    };

    // Report via NotificationService to surface it in the UI
    const notifications = this.injector.get(NotificationService);
    notifications.report(classifiedError, 'banner');
  }
}
