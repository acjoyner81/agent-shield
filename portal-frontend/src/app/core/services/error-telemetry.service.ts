import { Injectable, Injector } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { ErrorStateService } from '../core/errors/error-state.service';
import { take, tap } from 'rxjs/operators';

@Injectable({ providedIn: 'root' })
export class ErrorTelemetryService {
  private readonly telemetryUrl = '/v1/telemetry/logs';
  private readonly maxQueueSize = 10;
  private readonly flushInterval = 30000; // 30 seconds
  private readonly dedupeWindow = 60000; // 60 seconds

  private queue: Array<{ level: string; message: string; timestamp: number }> = [];
  private lastFlush = 0;
  private dedupeMap = new Map<string, number>();

  constructor(
    private http: HttpClient,
    private injector: Injector
  ) {
    this.errorStateService = this.injector.get(ErrorStateService);
    this.startFlushInterval();
  }

  private get errorStateService() {
    return this.injector.get(ErrorStateService);
  }

  private startFlushInterval() {
    setInterval(() => this.flushQueue(), this.flushInterval);
  }

  reportUncaughtError(kind: string, message: string, route: string): void {
    // Don't report if the error is from the telemetry endpoint itself (loop guard)
    if (route === this.telemetryUrl) {
      return;
    }

    // Dedupe: don't report the same kind+route within the dedupe window
    const dedupeKey = `${kind}:${route}`;
    const now = Date.now();
    const lastReport = this.dedupeMap.get(dedupeKey) || 0;
    if (now - lastReport < this.dedupeWindow) {
      return;
    }
    this.dedupeMap.set(dedupeKey, now);

    // Sanitize message: kind + route only, no stack traces or user input
    const sanitizedMessage = `[${kind}] ${route}`;

    const payload = {
      level: 'ERROR',
      message: sanitizedMessage,
      timestamp: Date.now()
    };

    // Fire-and-forget HTTP post with error handling
    this.http.post(this.telemetryUrl, payload)
      .pipe(
        take(1),
        tap({
          next: () => {
            // Successfully reported - could add to success tracking
          },
          error: (err) => {
            // Loop guard: if the telemetry POST itself fails, don't retry
            // (the error is already logged via console.error in GlobalErrorHandler)
          }
        })
      )
      .subscribe();
  }

  private flushQueue(): void {
    if (this.queue.length === 0) return;
    const batch = this.queue.splice(0, this.queue.length);
    // Batch send - each item is already deduplicated and sanitized
    batch.forEach(payload => {
      this.http.post(this.telemetryUrl, payload)
        .pipe(take(1))
        .subscribe({
          next: () => {},
          error: () => {} // Loop guard: silently fail
        });
    });
  }
}
