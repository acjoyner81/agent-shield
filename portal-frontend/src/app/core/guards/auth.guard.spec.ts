import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { Auth0ClientService } from '@auth0/auth0-angular';
import { firstValueFrom, of } from 'rxjs';

import { authGuard } from './auth.guard';

/**
 * The guard's contract.
 *
 * This exists because the guard is hand rolled rather than `authGuardFn`. The
 * stock guard asks `isAuthenticated()`, which in `@auth0/auth0-spa-js` 2.x is
 * `!!await getUser()` and `getUser()` reads a `user` claim that a modern OIDC ID
 * token does not carry, so the stock guard reports a valid session as signed out
 * and restarts authorization on every page load.
 *
 * So the two things worth pinning are that a cached session is admitted, and that
 * a missing one is still refused. Refusing is the part that must not regress: a
 * guard that admits everything would look identical in a demo and be a hole.
 */
describe('authGuard', () => {
  let claims: Record<string, unknown> | undefined;
  let client: { getIdTokenClaims: jasmine.Spy };
  let router: Router;

  const run = () =>
    TestBed.runInInjectionContext(() => authGuard(null as never, null as never)) as ReturnType<typeof of>;

  beforeEach(() => {
    claims = { sub: 'google-oauth2|1', name: 'A J' };
    client = { getIdTokenClaims: jasmine.createSpy('getIdTokenClaims').and.callFake(async () => claims) };

    TestBed.configureTestingModule({
      providers: [provideRouter([]), { provide: Auth0ClientService, useValue: client }],
    });
    router = TestBed.inject(Router);
  });

  it('admits the route when a session is cached', async () => {
    const result = await firstValueFrom(run());
    expect(result).toBe(true);
  });

  it('refuses the route when there is no cached session', async () => {
    claims = undefined;
    const result = await firstValueFrom(run());
    expect(result).toEqual(router.createUrlTree(['/']));
  });

  it('refuses rather than throwing when reading the cache fails', async () => {
    // A rejected guard observable cancels navigation outright, which would leave
    // the user on a blank page instead of being sent to sign in.
    client.getIdTokenClaims.and.callFake(async () => {
      throw new Error('cache unavailable');
    });
    const result = await firstValueFrom(run());
    expect(result).toEqual(router.createUrlTree(['/']));
  });
});
