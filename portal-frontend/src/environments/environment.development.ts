/**
 * Configuration for `ng serve` and `--configuration development` builds, wired
 * in through `fileReplacements` in `angular.json`. This file used to be dead
 * code: nothing imported it and no replacement rule pointed at it, so the
 * "development" environment was byte-identical to the shipping one.
 *
 * The Auth0 tenant is shared with the production file because only one exists.
 * `redirect_uri` is the same expression in both, so an Auth0 allowed callback
 * list does not have to be kept in step across environments.
 */
// `as const` on the value: `app.config.ts` spreads this object straight into
// `provideAuth0`, so without it TypeScript widens `cacheLocation` to `string`
// and the auth config fails to typecheck with "Type 'string' is not assignable
// to type 'CacheLocation'". That failure is invisible until a build runs.
export const environment = {
  production: false,
  auth0: {
    domain: 'dev-zymaiayb0afkpn7n.us.auth0.com',
    clientId: 'UMKcEHdjnSVuoZrqEDI158VQ4m3z3uJt',
    // Persist the session across reloads and restarts.
    //
    // auth0-spa-js defaults `cacheLocation` to `memory`, so every page load
    // discards the token set and silently restarts the PKCE exchange. On a
    // guarded route that bounces the user back to `/u/login` on refresh, and it
    // makes the app untestable: a browser-driven check cannot hold a session
    // across two page loads, so no failure state can ever be reached.
    //
    // Only set for local development. `localstorage` exposes the token to any
    // script on the origin, so the production file must keep the memory cache
    // (or move to a refresh-token rotation) rather than inherit this.
    cacheLocation: 'localstorage',
    // No `useRefreshTokens` here. It is the recommended pairing for a persisted
    // cache, but it requests `offline_access`, and the stored access token alone
    // is enough to survive a reload. Adding it is a deliberate follow-up, not an
    // oversight: it changes what the tenant consents to, so it should be a
    // decision rather than a default.
    //
    // Known and still open: a full page load of a guarded route restarts
    // authorization. `checkSession` misses the cached entry and falls back to its
    // documented full-page redirect to /authorize, and stock `authGuardFn` then
    // calls `loginWithRedirect` when `isAuthenticated$` is false. The token is
    // present and valid for 24h when this happens, so it is a cache lookup
    // problem, not an expired session. In-app navigation is unaffected, which is
    // how the Spec 0013 failure states are currently verified.
    authorizationParams: {
      audience: 'https://api.agentshield.local',
      redirect_uri: typeof window !== 'undefined' ? window.location.origin : 'http://localhost:4200'
    }
  }
} as const;
