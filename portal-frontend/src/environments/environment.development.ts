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
export const environment = {
  production: false,
  auth0: {
    domain: 'dev-zymaiayb0afkpn7n.us.auth0.com',
    clientId: 'UMKcEHdjnSVuoZrqEDI158VQ4m3z3uJt',
    authorizationParams: {
      audience: 'https://api.agentshield.local',
      redirect_uri: typeof window !== 'undefined' ? window.location.origin : 'http://localhost:4200'
    }
  }
};
