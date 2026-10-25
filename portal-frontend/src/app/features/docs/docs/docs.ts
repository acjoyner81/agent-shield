import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { HelpContentService, HelpCorpus } from '../../../core/services/help-content.service';

type ListLinkPart = { type: 'text' | 'apiLink' | 'routerLink'; text: string; url?: string };

@Component({
  selector: 'app-docs', standalone: true, imports: [RouterLink],
  templateUrl: './docs.html', styleUrl: './docs.css',
})
export class Docs {
  readonly corpus = signal<HelpCorpus | null>(null);
  readonly error = signal(false);
  readonly sections = computed(() =>
    (this.corpus()?.documents ?? []).flatMap((document) => document.sections),
  );
  constructor() {
    inject(HelpContentService).corpus$.subscribe({ next: (content) => this.corpus.set(content), error: () => this.error.set(true) });
  }

  renderListItem(item: string): ListLinkPart[] {
    const parts: ListLinkPart[] = [];
    const regex = /\[([^\]]+)\]\((\/api\/[^)]+|\/[^)]+)\)/g;
    let lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = regex.exec(item)) !== null) {
      if (match.index > lastIndex) {
        parts.push({ type: 'text', text: item.slice(lastIndex, match.index) });
      }
      const url = match[2];
      if (url.startsWith('/api/')) {
        parts.push({ type: 'apiLink', text: match[1], url });
      } else {
        parts.push({ type: 'routerLink', text: match[1], url });
      }
      lastIndex = regex.lastIndex;
    }
    if (lastIndex < item.length) {
      parts.push({ type: 'text', text: item.slice(lastIndex) });
    }
    if (parts.length === 0) {
      parts.push({ type: 'text', text: item });
    }
    return parts;
  }
}
