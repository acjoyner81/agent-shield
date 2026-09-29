import { Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { SILENT_POLL, USER_ACTION } from '../errors/surface.context';

export interface APIKey {
  key_id: string;
  name: string;
  key_prefix: string;
  created_at: string;
  status: string;
  secret_key?: string;
}

@Injectable({ providedIn: 'root' })
export class KeysService {
  private readonly apiUrl = '/api/v1/keys';
  readonly keys = signal<APIKey[]>([]);

  constructor(private readonly http: HttpClient) {}

  /**
   * A page load, not a click. A failure leaves the previous list on screen and
   * marks the list stale rather than interrupting, because the user did not ask
   * for this request and may be reading the keys that are already there.
   */
  fetchKeys(): void {
    this.http.get<APIKey[]>(this.apiUrl, { context: SILENT_POLL }).subscribe({
      next: (data) => this.keys.set(data),
      // Present but empty on purpose. The interceptor has already classified the
      // failure and reported it, and the previous list stays on screen. Without a
      // handler the interceptor's rethrow would surface as an unhandled rejection.
      // This is not the old bug: the difference is the declared SILENT_POLL
      // context, which is what makes the failure visible to the store.
      error: () => undefined,
    });
  }

  /** A click, so a refusal has to be visible. */
  createKey(name: string): Observable<APIKey> {
    return this.http.post<APIKey>(this.apiUrl, { name }, { context: USER_ACTION });
  }

  /** A click, so a refusal has to be visible. */
  revokeKey(keyId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/${keyId}`, { context: USER_ACTION });
  }
}