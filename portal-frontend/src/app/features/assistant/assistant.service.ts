import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface AssistantMessage { role: 'user' | 'assistant'; content: string; paragraphs?: Array<{ text: string; source_ids: string[] }>; citations?: Array<{ source_id: string; title: string; url: string }>; usage?: AssistantReply['usage']; }
export interface AssistantReply { paragraphs: Array<{ text: string; source_ids: string[] }>; citations: Array<{ source_id: string; title: string; url: string }>; usage: { input_tokens: number | null; output_tokens: number | null; cost_usd: number | null }; }

@Injectable({ providedIn: 'root' })
export class AssistantService {
  private readonly http = inject(HttpClient);
  ask(question: string, history: Array<{ role: 'user' | 'assistant'; content: string }>): Observable<AssistantReply> {
    return this.http.post<AssistantReply>('/api/v1/help/assistant', { question, history });
  }
}

export function retryDelaySeconds(error: unknown): number {
  if (!(error instanceof HttpErrorResponse) || error.status !== 429) return 0;
  const raw = error.headers.get('Retry-After');
  const seconds = raw ? Number(raw) : 0;
  return Number.isFinite(seconds) ? Math.max(0, Math.min(seconds, 300)) : 0;
}
