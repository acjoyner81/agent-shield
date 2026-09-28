import { Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

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

  fetchKeys(): void {
    this.http.get<APIKey[]>(this.apiUrl).subscribe({
      next: (data) => this.keys.set(data),
      error: (err) => console.warn('Failed to fetch API keys', err),
    });
  }

  createKey(name: string): Observable<APIKey> {
    return this.http.post<APIKey>(this.apiUrl, { name });
  }

  revokeKey(keyId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/${keyId}`);
  }
}