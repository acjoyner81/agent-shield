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

/**
 * Turn a failed assistant call into a message that names what actually went
 * wrong. Collapsing every failure into one sentence meant a 403 from a token
 * that carried no tenant claim looked identical to a provider outage, and the
 * user had no way to tell them apart. Each status the endpoint can return gets
 * its own sentence; the gateway's `detail` is appended when present.
 */
export function describeAssistantFailure(error: unknown): string {
  const detail = gatewayDetail(error);
  if (!(error instanceof HttpErrorResponse)) {
    return `The assistant could not be reached. Your question is saved; you can retry.${detail}`;
  }
  switch (error.status) {
    case 0:
      return `The assistant could not be reached. Check your connection, then retry.${detail}`;
    case 401:
      return `Your session was not accepted. Sign in again, then retry.${detail}`;
    case 403:
      return `Your account cannot use the assistant. A workspace owner may need to grant access.${detail}`;
    case 429:
      return `The assistant is rate limited. Your question is saved; retry when the wait ends.${detail}`;
    case 502:
      return `The assistant provider rejected the request. Your question is saved; you can retry.${detail}`;
    case 503:
      return `The assistant is temporarily unavailable. Your question is saved; you can retry.${detail}`;
    default:
      return `The assistant could not answer right now. Your question is saved; you can retry.${detail}`;
  }
}

function gatewayDetail(error: unknown): string {
  if (!(error instanceof HttpErrorResponse)) return '';
  const detail = (error.error as { detail?: unknown } | null)?.detail;
  return typeof detail === 'string' && detail ? ` (${detail})` : '';
}
