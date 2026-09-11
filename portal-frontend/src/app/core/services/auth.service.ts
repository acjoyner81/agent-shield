import { Injectable, computed, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { toSignal } from '@angular/core/rxjs-interop';
import { AuthService as Auth0Service } from '@auth0/auth0-angular';
import { Observable } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly auth0 = inject(Auth0Service);
  private readonly http = inject(HttpClient);
  readonly user = toSignal(this.auth0.user$);
  readonly authenticated = toSignal(this.auth0.isAuthenticated$, { initialValue: false });
  readonly userName = computed(() => this.user()?.name || this.user()?.nickname || this.user()?.email || 'Anthony Joyner');
  readonly tenantName = computed(() => 'AgentShield Enterprise');

  login(): void { void this.auth0.loginWithRedirect(); }
  logout(): void { void this.auth0.logout({ logoutParams: { returnTo: window.location.origin } }); }

  testPythonGateway(): Observable<unknown> {
    return this.http.get('/api/python/api/v1/protected');
  }

  testJavaGateway(): Observable<unknown> {
    return this.http.get('/api/java/actuator/health');
  }
}
