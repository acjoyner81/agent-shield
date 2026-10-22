import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { HelpContentService, HelpCorpus } from '../../../core/services/help-content.service';

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
}
