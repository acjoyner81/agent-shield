import { environment as dev } from './environment.development';
import { environment as prod } from './environment';

const AUDIENCE = 'https://api.agentshield.local';

/** The scope requested for the API audience, or undefined when none is set. */
const scopeFor = (env: typeof dev | typeof prod): string | undefined => {
  const scope = (env.auth0.authorizationParams as { scope?: string | Record<string, string> })
    .scope;
  return typeof scope === 'string' ? scope : scope?.[AUDIENCE];
};

describe('the Auth0 scope the portal asks for', () => {
  it('is requested under the API audience, not the default key', () => {
    // A string scope makes auth0-spa-js key its audience -> scope map by the
    // literal "default", so _getIdTokenFromCache builds a cache key with the
    // scope omitted and misses the entry that has one.
    expect(scopeFor(dev)).toBe('openid');
  });

  it('asks for no scope the app does not read', () => {
    // `openid profile email` looks harmless and is not. auth0-spa-js keys its
    // token cache by clientId::audience::scope, so a request for
    // `openid profile email` cannot see the token cached under `openid` and
    // falls through to a silent renewal on every call. A silent renewal is
    // prompt=none, which cannot render a consent screen, so Auth0 answered
    // consent_required, nothing was cached, and the SPA never issued the API
    // request at all. Every page rendered zeros and, because the polls are
    // SILENT_POLL, showed no error either.
    const requested = (scopeFor(dev) ?? 'openid').split(/\s+/);
    expect(requested).toEqual(['openid']);
  });

  it('matches what the shipping environment resolves to', () => {
    // The shipping file sets no scope, so the SDK default `openid` applies. The
    // dev build asking for more than production is how the two drifted apart in
    // the first place.
    expect(scopeFor(prod) ?? 'openid').toBe(scopeFor(dev) ?? 'openid');
  });

  it('carries the tenant claim the gateway requires', () => {
    // Dropping a scope must never cost the claim that authenticates the caller
    // against the API. gateway/auth.py reads it from the access token.
    expect(dev.auth0.authorizationParams.audience).toBe(AUDIENCE);
  });
});
