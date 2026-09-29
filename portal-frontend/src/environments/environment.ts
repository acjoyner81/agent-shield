/**
 * Configuration for the default (production) build.
 *
 * `ng build` resolves to the `production` configuration in `angular.json`, which
 * has no `fileReplacements`, so this is the file that ships. The `development`
 * configuration swaps in `environment.development.ts`.
 *
 * Note that only `environment.auth0` is consumed today; `app.config.ts` spreads
 * it into `provideAuth0` and reads nothing else. The `production` flag is kept
 * because it is the conventional place a deployment records which build it is,
 * but flipping it does not change runtime behavior on its own. What actually
 * separates a production bundle from a dev one is the `production` configuration
 * itself (optimization, output hashing, budgets), not this field.
 */
export const environment = {
  production: true,
  auth0: {
    // Single tenant for the whole stack today, local compose included. A real
    // deployment must point this at its own Auth0 tenant; the audience and the
    // client id are the same values the gateway validates, so changing only one
    // side produces a token the API rejects.
    domain: 'dev-zymaiayb0afkpn7n.us.auth0.com',
    clientId: 'UMKcEHdjnSVuoZrqEDI158VQ4m3z3uJt',
    authorizationParams: {
      audience: 'https://api.agentshield.local',
      redirect_uri: typeof window !== 'undefined' ? window.location.origin : 'http://localhost:4200'
    }
  }
};
