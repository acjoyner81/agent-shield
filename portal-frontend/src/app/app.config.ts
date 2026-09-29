import { ApplicationConfig, provideBrowserGlobalErrorListeners, provideZoneChangeDetection } from '@angular/core';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideRouter } from '@angular/router';
import { provideAuth0, authHttpInterceptorFn } from '@auth0/auth0-angular';

import { routes } from './app.routes';
import { environment } from '../environments/environment';
import { gatewayErrorInterceptor } from './core/interceptors/gateway-error.interceptor';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideRouter(routes),
    provideHttpClient(
      // Order is load bearing (Spec 0013). `authHttpInterceptorFn` attaches the
      // bearer token, so ours must sit after it and see responses that carry an
      // Authorization header. Reversed, the Auth0 token failure path is misread
      // as a gateway failure and reported as a gateway outage.
      withInterceptors([authHttpInterceptorFn, gatewayErrorInterceptor])
    ),
    provideAuth0({
      ...environment.auth0,
      httpInterceptor: {
        allowedList: [
          {
            uri: '/api/*',
            tokenOptions: {
              authorizationParams: {
                audience: 'https://api.agentshield.local'
              }
            }
          }
        ]
      }
    }),
  ]
};
