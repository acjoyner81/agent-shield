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
    // A full page load of a guarded route works because of the audience-keyed
    // `scope` below. With a string scope it silently does not: the app restarts
    // authorization on every reload while looking correctly configured. That is
    // the whole reason this file is not just a copy of the production one.
    authorizationParams: {
      audience: 'https://api.agentshield.local',
      // Keyed BY AUDIENCE, and this is load bearing.
      //
      // `auth0-spa-js` builds an audience -> scope map at construction via
      // `injectDefaultScopes(authorizationParams.scope, 'openid', ...)`. Given a
      // STRING it keys that map by the literal `DEFAULT_AUDIENCE` ("default") and
      // nothing else. `_getIdTokenFromCache` then looks up
      // `this.scope['https://api.agentshield.local']`, gets `undefined`, and
      // builds a cache key with the scope omitted, so the lookup misses an entry
      // whose key does include the scope.
      //
      // The object form keys the map by the real audience. Confirmed by reading
      // the SDK: `getUniqueScopes('openid', 'openid profile email', <empty session
      // scope>)` dedupes to exactly `'openid profile email'`, which is the string
      // the stored entry is keyed by.
      //
      // STILL OPEN, and this comment should not be read as claiming otherwise: a
      // full page load of a guarded route still restarts authorization. Keying the
      // scope correctly is necessary but not sufficient. A deeper, separate
      // defect sits behind it -- the gateway answers every authenticated call
      // with 403 "Token missing mandatory tenant identification claim", because
      // this Auth0 tenant issues no tenant claim for the API to read. Until the
      // token carries one, the portal has an authenticated session it cannot use.
      // See `core/guards/auth.guard.ts` for why `isAuthenticated$` is unusable
      // regardless.
      //
      // A string here is the trap: it typechecks, and it looks right.
      scope: {
        'https://api.agentshield.local': 'openid profile email',
      },
      redirect_uri: typeof window !== 'undefined' ? window.location.origin : 'http://localhost:4200'
    }
  }
} as const;
