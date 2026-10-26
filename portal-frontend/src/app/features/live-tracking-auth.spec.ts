import { HttpInterceptorFn, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed, fakeAsync, tick } from '@angular/core/testing';

import { AgentRuntimeComponent } from './agent-runtime/agent-runtime.component';
import { EvaluationsComponent } from './evaluations/evaluations.component';

/**
 * Live-tracking pages must call the API through the app-level `HttpClient`, the
 * one `provideHttpClient(withInterceptors(...))` configured in `app.config.ts`.
 *
 * A standalone component that also lists `HttpClientModule` in its own `imports`
 * gets a second, component-scoped `HttpClient` that has none of the app's
 * functional interceptors. That is invisible until the gateway refuses the call:
 * the Auth0 interceptor never runs, so no `Authorization` header is attached and
 * `/v1/evals/recent` and `/v1/agent/executions` answer 401 while `/v1/health`
 * (fetched from a root service) keeps working. These tests fail if that module
 * import is reintroduced.
 */
describe('live tracking pages use the app-level HTTP interceptors', () => {
  const cases: ReadonlyArray<readonly [string, unknown, string]> = [
    ['evaluations', EvaluationsComponent, '/api/v1/evals/recent'],
    ['agent runtime', AgentRuntimeComponent, '/api/v1/agent/executions'],
  ];

  for (const [name, component, url] of cases) {
    it(
      `${name} routes ${url} through the root interceptor chain`,
      fakeAsync(() => {
        const handled: string[] = [];
        const probe: HttpInterceptorFn = (req, next) => {
          handled.push(req.url);
          return next(req);
        };

        TestBed.configureTestingModule({
          imports: [component as never],
          providers: [provideHttpClient(withInterceptors([probe])), provideHttpClientTesting()],
        });

        const httpMock = TestBed.inject(HttpTestingController);
        const fixture = TestBed.createComponent(component as never);
        fixture.detectChanges();

        // `timer(0, …)` schedules its first emit on the async scheduler; run the
        // initial tick so the component's request is actually made.
        tick(0);

        const requests = httpMock.match(url);
        expect(requests.length).toBe(1);
        expect(handled).toContain(url);

        requests.forEach((request) => request.flush([]));
        fixture.destroy();
        httpMock.verify();
      }),
    );
  }
});
