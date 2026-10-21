import { Injectable, computed, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { toSignal } from '@angular/core/rxjs-interop';
import { AuthService as Auth0Service } from '@auth0/auth0-angular';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';

/**
 * The namespaced permissions claim, mirroring `PERMISSIONS_CLAIM` in
 * `gateway/auth.py`.
 *
 * It has to be namespaced: Auth0 reserves the bare `permissions` name for its
 * own RBAC and silently drops a custom claim that collides with it, which is
 * exactly how real user tokens once arrived carrying no scopes at all.
 */
const PERMISSIONS_CLAIM = 'https://api.agentshield.local/permissions';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly auth0 = inject(Auth0Service);
  private readonly http = inject(HttpClient);
  readonly user = toSignal(this.auth0.user$);
  readonly authenticated = toSignal(this.auth0.isAuthenticated$, { initialValue: false });
  readonly userName = computed(() => this.user()?.name || this.user()?.nickname || this.user()?.email || 'Anthony Joyner');
  readonly tenantName = computed(() => 'AgentShield Enterprise');

  /**
   * The scopes the signed-in principal holds, from the ID token's namespaced
   * claim.
   *
   * The keys page uses this to decide what a key may be granted: the gateway
   * refuses any scope the caller does not hold, so offering one in the UI would
   * be an affordance that can only fail. An absent claim yields an empty list,
   * which fails closed, matching the gateway.
   */
  readonly permissions = toSignal(
    this.auth0.idTokenClaims$.pipe(
      map((claims) => {
        const raw = (claims as Record<string, unknown> | null | undefined)?.[PERMISSIONS_CLAIM];
        return Array.isArray(raw) ? raw.filter((scope): scope is string => typeof scope === 'string') : [];
      }),
    ),
    { initialValue: [] as string[] },
  );

  login(): void { void this.auth0.loginWithRedirect(); }
  logout(): void { void this.auth0.logout({ logoutParams: { returnTo: window.location.origin } }); }

  testPythonGateway(): Observable<unknown> {
    return this.http.get('/api/python/api/v1/protected');
  }

  testJavaGateway(): Observable<unknown> {
    return this.http.get('/api/java/actuator/health');
  }
}
