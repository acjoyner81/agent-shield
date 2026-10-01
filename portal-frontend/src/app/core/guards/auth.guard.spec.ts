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
 * a missing one still starts a login. The second matters just as much: an
 * earlier version of this guard answered a missing session with
 * `createUrlTree(['/'])`, and since `''` redirects to the guarded `dashboard`
 * route, that bounced `/` -> `/dashboard` -> `/` in a loop. The tests below hold
 * the sign-in path in place.
 */
describe('authGuard', () => {
  let claims: Record<string, unknown> | undefined;
  let client: { getIdTokenClaims: jasmine.Spy; loginWithRedirect: jasmine.Spy };
  let router: Router;

  const run = () =>
    TestBed.runInInjectionContext(() => authGuard(null as never, null as never)) as ReturnType<typeof of>;

  beforeEach(() => {
    claims = { sub: 'google-oauth2|1', name: 'A J' };
    client = {
      getIdTokenClaims: jasmine.createSpy('getIdTokenClaims').and.callFake(async () => claims),
      loginWithRedirect: jasmine.createSpy('loginWithRedirect').and.resolveTo(undefined),
    };

    TestBed.configureTestingModule({
      providers: [provideRouter([]), { provide: Auth0ClientService, useValue: client }],
    });
    router = TestBed.inject(Router);
  });

  it('admits the route when a session is cached', async () => {
    const result = await firstValueFrom(run());
    expect(result).toBe(true);
  });

  it('does not start a login when a session is cached', async () => {
    await firstValueFrom(run());
    expect(client.loginWithRedirect).not.toHaveBeenCalled();
  });

  it('refuses the route and starts a login when there is no cached session', async () => {
    claims = undefined;
    const result = await firstValueFrom(run());
    expect(result).toBe(false);
    expect(client.loginWithRedirect).toHaveBeenCalledTimes(1);
  });

  it('refuses rather than dead-ending when reading the cache fails', async () => {
    // A rejected guard observable cancels navigation outright, which would leave
    // the user on a blank page instead of being sent to sign in.
    client.getIdTokenClaims.and.callFake(async () => {
      throw new Error('cache unavailable');
    });
    const result = await firstValueFrom(run());
    expect(result).toBe(false);
    expect(client.loginWithRedirect).toHaveBeenCalledTimes(1);
  });

  it('never returns a UrlTree, which would loop against the / redirect', async () => {
    claims = undefined;
    const result = await firstValueFrom(run());
    expect(result).not.toEqual(router.createUrlTree(['/']));
  });
});
