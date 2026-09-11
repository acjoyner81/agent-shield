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
