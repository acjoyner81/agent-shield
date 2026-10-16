import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { Auth0ClientService } from '@auth0/auth0-angular';
import { firstValueFrom, of } from 'rxjs';

import { authGuard } from './auth.guard';
import { SESSION_REDIRECT_LATCH_KEY } from '../errors/error-state.service';

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
  let client: { getIdTokenClaims: jasmine.Spy; loginWithRedirect: jasmine.Spy; handleRedirectCallback: jasmine.Spy };
  let router: Router;
  let startUrl: string;

  const run = () =>
    TestBed.runInInjectionContext(() => authGuard(null as never, null as never)) as ReturnType<typeof of>;

  beforeEach(() => {
    claims = { sub: 'google-oauth2|1', name: 'A J' };
    client = {
      getIdTokenClaims: jasmine.createSpy('getIdTokenClaims').and.callFake(async () => claims),
      loginWithRedirect: jasmine.createSpy('loginWithRedirect').and.resolveTo(undefined),
      handleRedirectCallback: jasmine.createSpy('handleRedirectCallback').and.resolveTo({}),
    };

    TestBed.configureTestingModule({
      providers: [provideRouter([]), { provide: Auth0ClientService, useValue: client }],
    });
    router = TestBed.inject(Router);
    startUrl = window.location.href;
  });

  afterEach(() => {
    window.history.replaceState({}, '', startUrl);
    localStorage.removeItem(SESSION_REDIRECT_LATCH_KEY);
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

  it('exchanges a +code/state callback before answering about the session', async () => {
    // The redirect from Auth0 lands back with the session still in the URL; the
    // ID token cache is empty until the SDK turns that code into a cache entry.
    // Answering before that exchange happened used to read "no claims" and send
    // the user back to authorize: every landed page re-initiated the next one.
    let callbackExchanged = false;
    client.handleRedirectCallback.and.callFake(async () => {
      callbackExchanged = true;
    });
    client.getIdTokenClaims.and.callFake(async () => (callbackExchanged ? claims : undefined));
    window.history.replaceState({}, '', '/dashboard?code=abc&state=xyz');

    const result = await firstValueFrom(run());

    expect(result).toBe(true);
    expect(client.handleRedirectCallback).toHaveBeenCalledTimes(1);
    expect(client.loginWithRedirect).not.toHaveBeenCalled();
  });

  it('signs in rather than dead-ending when the exchange itself fails', async () => {
    // A stale or tampered callback: the SDK rejects, no claims exist, and the
    // guard must still refuse the route into a login, never a blank page.
    client.handleRedirectCallback.and.callFake(async () => {
      throw new Error('transaction not found');
    });
    claims = undefined;
    window.history.replaceState({}, '', '/dashboard?code=abc&state=stale');

    const result = await firstValueFrom(run());

    expect(result).toBe(false);
    expect(client.loginWithRedirect).toHaveBeenCalledTimes(1);
  });

  it('clears a spent-redirect latch when a brand new sign-in begins', async () => {
    // The latch lives in local storage, so it survives a closed and reopened tab
    // next to the dead token that prompted the redirect. A genuinely fresh sign
    // in is a new session and the latch belonged to the one that died; a spent
    // latch that survives a new exchange would quietly swallow the one automatic
    // redirect the fresh session is owed.
    localStorage.setItem(SESSION_REDIRECT_LATCH_KEY, '1');
    claims = undefined;

    const result = await firstValueFrom(run());

    expect(result).toBe(false);
    expect(localStorage.getItem(SESSION_REDIRECT_LATCH_KEY)).toBeNull();
    expect(client.loginWithRedirect).toHaveBeenCalledTimes(1);
  });
});
