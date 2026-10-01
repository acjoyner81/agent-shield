import { inject } from '@angular/core';
import { CanActivateFn } from '@angular/router';
import { Auth0ClientService } from '@auth0/auth0-angular';
import { catchError, from, map, of } from 'rxjs';

/**
 * Route guard for authenticated pages.
 *
 * This deliberately does NOT use `authGuardFn`, and the reason is not a
 * preference. In `@auth0/auth0-spa-js` 2.x `isAuthenticated()` is implemented as
 * `!!await this.getUser()`, and `getUser()` returns `idToken.decodedToken.user`.
 * A modern OIDC ID token has no `user` claim: the profile claims (`name`, `email`,
 * `picture`, `given_name`, ...) sit at the top level, not nested under `user`.
 * So for a token issued for a custom API audience the chain is:
 *
 *   getUser()            -> undefined
 *   isAuthenticated()    -> false
 *   isAuthenticated$     -> emits false
 *   stock AuthGuard      -> loginWithRedirect()
 *
 * `authGuardFn` therefore reports a perfectly valid session as signed out and
 * restarts the PKCE exchange on every full page load of a guarded route. It is
 * not a cache miss: the token is present and valid. Verified against
 * `@auth0/auth0-spa-js` 2.24.1 and 2.27.0, which are identical here, so there is
 * no version to upgrade to. `isAuthenticated$` is not used anywhere else in this
 * app for the same reason (see `core/services/auth.service.ts`).
 *
 * `getIdTokenClaims()` is a pure cache read of the same entry and does not
 * depend on a `user` claim, so it answers the question the guard is actually
 * asking: is there a session?
 *
 * Expiry is not checked here. An expired ID token still yields claims, so the
 * guard admits the route and the first real API call fails with a 401, which
 * Spec 0013's interceptor turns into exactly one re-authentication redirect.
 * That is the intended division of labour: the guard answers "is there a
 * session", the gateway answers "is it still good", and only the gateway knows.
 *
 * This is necessary, not sufficient. A full page load of a guarded route still
 * restarts authorization after this change; see the note on `scope` in
 * `environments/environment.development.ts` for what remains.
 */
export const authGuard: CanActivateFn = () => {
  const client = inject(Auth0ClientService);

  // There is no signed-out landing page to send anyone to: `''` redirects to
  // `dashboard`, which is guarded, so a `UrlTree` aimed at `/` bounces straight
  // back here and spins. Start the login flow instead and refuse the route,
  // which is what the stock guard did once it had correctly decided the user is
  // signed out.
  const signIn = () => {
    void Promise.resolve(client.loginWithRedirect()).catch(() => undefined);
    return false;
  };

  return from(client.getIdTokenClaims()).pipe(
    map((claims) => (claims ? true : signIn())),
    // A cache read should not reject, but a rejected guard observable cancels
    // navigation outright and leaves the user on a blank page. An unreadable
    // cache is still an absent session, so sign in rather than dead-end.
    catchError(() => of(signIn())),
  );
};
