import { inject } from '@angular/core';
import { CanActivateFn } from '@angular/router';
import { Auth0ClientService } from '@auth0/auth0-angular';
import { catchError, from, map, mergeMap, of } from 'rxjs';
import { SESSION_REDIRECT_LATCH_KEY } from '../errors/error-state.service';

/**
 * A redirect from Auth0 lands back on the app with `?code` and `?state`, and the
 * session only exists once the SDK has turned that code into a cache entry. The
 * guard answers "is there a session" by reading the ID token cache, so it has to
 * let the SDK perform that exchange first. A callback page read directly would
 * see no claims, call `loginWithRedirect`, and click the user through one
 * authorize round trip for every page load, consuming each code the moment it
 * arrives. Measured in the browser at 261 token exchanges in about eight
 * seconds, because every landed page immediately re-initiated the next.
 *
 * The exchange is idempotent in practice: the SDK only keeps one pending
 * transaction, the guard only runs once per callback page, and a second call
 * after the transaction is consumed throws, is swallowed below, and the claims
 * read still finds the token the successful call cached.
 */
const hasRedirectCallback = (): boolean =>
  new URLSearchParams(window.location.search).has('code') &&
  new URLSearchParams(window.location.search).has('state');

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
  //
  // A fresh sign-in supersedes a spent redirect: the latch exists to say "a
  // redirect already went out for the session this token represents". A brand
  // new exchange is a new session, and the latch belongs to the one that died.
  // This is what rescues a reopened tab, where local storage still holds the
  // latch next to a dead token.
  const signIn = () => {
    try {
      localStorage.removeItem(SESSION_REDIRECT_LATCH_KEY);
    } catch {
      // Storage being unavailable is not a reason to refuse the login flow.
    }
    void Promise.resolve(client.loginWithRedirect()).catch(() => undefined);
    return false;
  };

  return from(
    // If this page load IS the callback, let the SDK exchange the code before
    // the cache is asked anything. No-op on an ordinary page load.
    hasRedirectCallback()
      ? client.handleRedirectCallback().catch(() => undefined)
      : Promise.resolve(),
  ).pipe(
    mergeMap(() =>
      // Ensure the SDK is fully initialized before checking claims.
      // This prevents the guard from admitting a route while the token
      // is still being refreshed in the background, which would trigger
      // a 401 and a subsequent re-auth redirect 1 second later.
      from(client.getTokenSilently()).pipe(
        mergeMap(() => from(client.getIdTokenClaims())),
        map((claims) => (claims ? true : signIn())),
        catchError(() => of(signIn())),
      ),
    ),
    catchError(() => of(signIn())),
  );
};
