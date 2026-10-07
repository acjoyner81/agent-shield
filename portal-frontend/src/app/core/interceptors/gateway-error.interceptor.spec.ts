import { TestBed } from '@angular/core/testing';
import {
  HttpClient,
  HttpContext,
  provideHttpClient,
  withInterceptors,
} from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { gatewayErrorInterceptor } from './gateway-error.interceptor';
import { NotificationService } from '../errors/error-state.service';
import { SILENT_POLL, SURFACE, USER_ACTION } from '../errors/surface.context';
import { GatewayError } from '../errors/gateway-error';

/**
 * The interceptor's contract, which is mostly about what it must not touch.
 *
 * Three rules earn their own tests here:
 * - It only claims `/api/v1/`. A Spring Boot 401 arriving on `/api/java/...` must
 *   not become a gateway session expiry and drag the user to Auth0.
 * - It does not render. It classifies and applies a declared policy, so a
 *   background poll and a clicked button can reach the same status differently.
 * - It rethrows, so a caller can still handle its own failure.
 */

describe('gatewayErrorInterceptor', () => {
  let http: HttpClient;
  let httpMock: HttpTestingController;
  let notifications: NotificationService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([gatewayErrorInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpClient);
    httpMock = TestBed.inject(HttpTestingController);
    notifications = TestBed.inject(NotificationService);
  });

  afterEach(() => {
    notifications.ngOnDestroy();
    httpMock.verify();
  });

  function lastError(): GatewayError | undefined {
    return (notifications.toast() ?? notifications.budgetBanner() ?? undefined) as
      | GatewayError
      | undefined;
  }

  describe('scope', () => {
    it('leaves a non gateway request completely untouched', () => {
      let seen: unknown = null;
      http.get('/api/java/actuator/health').subscribe({ error: (err) => (seen = err) });
      httpMock
        .expectOne('/api/java/actuator/health')
        .flush({ detail: 'unauthorized' }, { status: 401, statusText: 'Unauthorized' });

      // No redirect, no notice: this is Spring Boot's own auth, not the gateway's.
      expect(notifications.sessionExpired()).toBe(false);
      expect(notifications.hasAnyNotice()).toBe(false);
      expect(seen).not.toBeNull();
    });

    it('leaves a Spring Boot 401 out of the session expiry path', () => {
      http.get('/api/python/api/v1/protected').subscribe({ error: () => undefined });
      httpMock
        .expectOne('/api/python/api/v1/protected')
        .flush({ detail: 'nope' }, { status: 401, statusText: 'Unauthorized' });

      expect(notifications.sessionExpired()).toBe(false);
    });

    it('still claims a gateway path under /api/v1/', () => {
      // USER_ACTION, not SILENT_POLL: a silent poll raises nothing by design, and
      // this test is about which paths the interceptor claims at all.
      http.get('/api/v1/usage/summary', { context: USER_ACTION }).subscribe({ error: () => undefined });
      httpMock
        .expectOne('/api/v1/usage/summary')
        .flush({ detail: 'gone' }, { status: 402, statusText: 'Payment Required' });

      expect(notifications.budgetBanner()).not.toBeNull();
    });
  });

  describe('policy is declared, not inferred', () => {
    it('raises nothing for a silent poll', () => {
      http.get('/api/v1/usage/summary', { context: SILENT_POLL }).subscribe({ error: () => undefined });
      httpMock
        .expectOne('/api/v1/usage/summary')
        .flush({ detail: 'throttled' }, { status: 429, statusText: 'Too Many Requests' });

      expect(notifications.hasAnyNotice()).toBe(false);
    });

    it('marks the widget stale for that same silent poll', () => {
      http.get('/api/v1/usage/summary', { context: SILENT_POLL }).subscribe({ error: () => undefined });
      httpMock
        .expectOne('/api/v1/usage/summary')
        .flush(null, { status: 500, statusText: 'Server Error' });

      expect(notifications.isStale('usage')).toBe(true);
    });

    it('maps the telemetry path to the logs widget', () => {
      http.get('/api/v1/telemetry/logs', { context: SILENT_POLL }).subscribe({ error: () => undefined });
      httpMock
        .expectOne('/api/v1/telemetry/logs')
        .flush(null, { status: 500, statusText: 'Server Error' });

      expect(notifications.isStale('logs')).toBe(true);
      expect(notifications.isStale('usage')).toBe(false);
    });

    it('maps the bare keys path to the keys widget', () => {
      // The list is `/api/v1/keys` with no trailing segment, so a map written
      // with trailing slashes like the others misses it and the list keeps its
      // previous keys with nothing saying they are old.
      http.get('/api/v1/keys', { context: SILENT_POLL }).subscribe({ error: () => undefined });
      httpMock.expectOne('/api/v1/keys').flush(null, { status: 500, statusText: 'Server Error' });

      expect(notifications.isStale('keys')).toBe(true);
    });

    it('treats a revoke as the same widget, since it redraws the same list', () => {
      // A revoke is a click, so a refused one toasts rather than going quiet. What
      // the mapping buys is on the success path: the revoke reloads the list, so
      // it has to clear the keys widget's stale flag with it.
      notifications.markStale('keys');

      http.delete('/api/v1/keys/key_abc', { context: USER_ACTION }).subscribe();
      httpMock.expectOne('/api/v1/keys/key_abc').flush(null, { status: 204, statusText: 'No Content' });

      expect(notifications.isStale('keys')).toBe(false);
    });

    it('shows a banner for a user initiated click on the same 429', () => {
      http.post('/api/v1/keys', { name: 'Staging' }, { context: USER_ACTION }).subscribe({ error: () => undefined });
      httpMock
        .expectOne('/api/v1/keys')
        .flush({ detail: 'throttled' }, { status: 429, statusText: 'Too Many Requests' });

      expect(notifications.rateLimit()).not.toBeNull();
    });

    it('is loud by default, so a call site that declares nothing is not silent', () => {
      http.get('/api/v1/keys').subscribe({ error: () => undefined });
      httpMock.expectOne('/api/v1/keys').flush({ detail: 'refused' }, { status: 403, statusText: 'Forbidden' });

      expect(notifications.toast()).not.toBeNull();
    });
  });

  describe('session expiry overrides a silent policy', () => {
    it('reports a 401 arriving on a background poll', () => {
      http.get('/api/v1/usage/summary', { context: SILENT_POLL }).subscribe({ error: () => undefined });
      httpMock
        .expectOne('/api/v1/usage/summary')
        .flush({ detail: 'expired' }, { status: 401, statusText: 'Unauthorized' });

      // The poll is the only request left when a tab sits idle, so suppressing
      // this would strand the user on a dead session with no explanation.
      expect(notifications.sessionExpired()).toBe(true);
    });
  });

  describe('success', () => {
    it('clears a sticky rate limit, so the banner cannot outlive the throttle', () => {
      http.get('/api/v1/keys', { context: USER_ACTION }).subscribe({ error: () => undefined });
      httpMock
        .expectOne('/api/v1/keys')
        .flush({ detail: 'throttled' }, { status: 429, statusText: 'Too Many Requests' });
      expect(notifications.rateLimit()).not.toBeNull();

      http.get('/api/v1/keys', { context: USER_ACTION }).subscribe();
      httpMock.expectOne('/api/v1/keys').flush([]);

      expect(notifications.rateLimit()).toBeNull();
    });

    it('marks the widget fresh on a successful poll', () => {
      http.get('/api/v1/usage/summary', { context: SILENT_POLL }).subscribe();
      httpMock.expectOne('/api/v1/usage/summary').flush({});

      expect(notifications.isStale('usage')).toBe(false);
      expect(notifications.lastUpdatedAt('usage')).not.toBeNull();
    });

    it('takes the keys list back out of the stale state', () => {
      http.get('/api/v1/keys', { context: SILENT_POLL }).subscribe({ error: () => undefined });
      httpMock.expectOne('/api/v1/keys').flush(null, { status: 500, statusText: 'Server Error' });
      expect(notifications.isStale('keys')).toBe(true);

      http.get('/api/v1/keys', { context: SILENT_POLL }).subscribe();
      httpMock.expectOne('/api/v1/keys').flush([]);

      expect(notifications.isStale('keys')).toBe(false);
      expect(notifications.staleNote('keys')).toBeNull();
    });
  });

  describe('the error the caller receives', () => {
    it('is the typed error, not the raw HttpErrorResponse', () => {
      let seen: unknown = null;
      http.get('/api/v1/keys', { context: USER_ACTION }).subscribe({ error: (err) => (seen = err) });
      httpMock
        .expectOne('/api/v1/keys')
        .flush({ detail: 'Key creation requires keys:write' }, { status: 403, statusText: 'Forbidden' });

      expect((seen as GatewayError).kind).toBe('scope_denied');
      expect((seen as GatewayError).status).toBe(403);
    });

    it('carries the original response for devtools, without it reaching the message', () => {
      let seen: GatewayError | null = null;
      http.get('/api/v1/keys', { context: USER_ACTION }).subscribe({ error: (err) => (seen = err) });
      httpMock
        .expectOne('/api/v1/keys')
        .flush({ detail: 'Key creation requires keys:write' }, { status: 403, statusText: 'Forbidden' });

      expect(seen!.originalError).not.toBeNull();
      // The message comes from the body, not from the retained raw response.
      expect(seen!.message).toBe('Key creation requires keys:write');
    });

    it('reaches a fire-and-forget caller through a synchronous error callback', () => {
      // The services subscribe with `error: () => undefined`, so the rethrow has
      // a handler and never becomes an unhandled rejection. Asserting the
      // handler runs synchronously is what keeps that true.
      let handled = false;
      http.get('/api/v1/keys', { context: USER_ACTION }).subscribe({
        error: () => (handled = true),
      });
      httpMock.expectOne('/api/v1/keys').flush(null, { status: 500, statusText: 'Server Error' });

      expect(handled).toBe(true);
    });
  });

  describe('the SURFACE token default', () => {
    it('is toast, so a call site that forgets the context is loud rather than silent', () => {
      // A brand new gateway call that never sets SURFACE gets this value, which
      // is what makes "loud by default" true rather than aspirational.
      expect(new HttpContext().get(SURFACE)).toBe('toast');
    });
  });
});
