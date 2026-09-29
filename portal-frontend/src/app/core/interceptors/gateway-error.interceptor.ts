import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, tap, throwError } from 'rxjs';

import { classifyGatewayError } from '../errors/classify';
import { SURFACE } from '../errors/surface.context';
import { NotificationService, StaleWidget } from '../errors/error-state.service';

/**
 * The only place a gateway failure is turned into something the user sees.
 *
 * It classifies, applies the policy the call site declared, and rethrows, so the
 * caller's own error handling still runs. It never renders: a response carries
 * no information about its origin, and the gateway's rate limiter is an app level
 * dependency, so a 429 from the dashboard's background poll is
 * indistinguishable here from a 429 the user caused by clicking. That
 * distinction is declared per call site through `SURFACE` and is the reason this
 * is not a renderer.
 */

/**
 * The gateway's own path prefix.
 *
 * `/api` is the ingress prefix for all three backends, and the portal also calls
 * `/api/python/api/v1/protected` and `/api/java/actuator/health` through it.
 * Scoping to `/api` would classify a Spring Boot 401 as a gateway session expiry
 * and redirect to Auth0 to fix a service that was never involved, naming the
 * wrong system in front of the user. `/api/v1/` is exactly where the gateway's
 * `billing`, `metering`, and `keys` mounts land.
 */
const GATEWAY_PREFIX = '/api/v1/';

/** Which stale widget a given path belongs to, so a silent poll knows its own name. */
const WIDGET_BY_PATH: ReadonlyArray<readonly [string, StaleWidget]> = [
  ['/api/v1/usage/', 'usage'],
  ['/api/v1/health/', 'health'],
  ['/api/v1/telemetry/', 'logs'],
];

function widgetFor(url: string): StaleWidget | undefined {
  return WIDGET_BY_PATH.find(([prefix]) => url.includes(prefix))?.[1];
}

export const gatewayErrorInterceptor: HttpInterceptorFn = (req, next) => {
  // Anything that is not the Python gateway passes through untouched.
  if (!req.url.startsWith(GATEWAY_PREFIX)) {
    return next(req);
  }

  const notifications = inject(NotificationService);
  const policy = req.context.get(SURFACE);

  return next(req).pipe(
    tap({
      // The first success clears a sticky 429, so a banner cannot keep claiming
      // the tenant is throttled once their requests are already succeeding.
      next: () => {
        const widget = widgetFor(req.url);
        if (widget !== undefined) {
          notifications.markFresh(widget);
        }
        notifications.reportSuccess();
      },
    }),
    catchError((err: unknown) => {
      const classified = classifyGatewayError(err);

      // Not an HttpErrorResponse, so not ours to interpret. Rethrow the original
      // so a non gateway failure keeps its own error semantics.
      if (classified === null) {
        return throwError(() => err);
      }

      notifications.report(classified, policy, widgetFor(req.url));

      // Rethrown so the caller can still handle it, and so a caller that never
      // subscribed to an error does not get an unhandled rejection. The value is
      // the typed error, so an `error` callback can read `kind` directly.
      return throwError(() => classified);
    }),
  );
};
