import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, shareReplay } from 'rxjs';

export interface HelpBlock { type: 'paragraph' | 'list' | 'code'; text?: string; parts?: Array<{ text: string; url?: string }>; items?: string[]; }
export interface HelpSection { source_id: string; section_id: string; document_id: string; title: string; heading: string; body: string; blocks: HelpBlock[]; }
export interface HelpDocument { document_id: string; title: string; sections: HelpSection[]; }
export interface HelpCorpus { documents: HelpDocument[]; sections: HelpSection[]; suggestions: Array<{ question: string; section_id: string }>; }

@Injectable({ providedIn: 'root' })
export class HelpContentService {
  private readonly http = inject(HttpClient);
  readonly corpus$: Observable<HelpCorpus> = this.http.get<HelpCorpus>('/help-center-content.json').pipe(shareReplay({ bufferSize: 1, refCount: true }));
}
