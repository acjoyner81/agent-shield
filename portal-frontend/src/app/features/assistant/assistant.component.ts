import { Component, OnDestroy, inject, signal } from '@angular/core';
import { NavigationStart, Router, RouterLink } from '@angular/router';
import { Subscription, filter } from 'rxjs';
import { AssistantMessage, AssistantService, describeAssistantFailure, retryDelaySeconds } from './assistant.service';
import { HelpContentService, HelpCorpus } from '../../core/services/help-content.service';

@Component({
  selector: 'app-assistant', standalone: true, imports: [RouterLink],
  templateUrl: './assistant.component.html', styleUrl: './assistant.component.css',
})
export class AssistantComponent implements OnDestroy {
  private readonly assistant = inject(AssistantService);
  private readonly content = inject(HelpContentService);
  private readonly router = inject(Router);
  readonly corpus = signal<HelpCorpus | null>(null);
  readonly messages = signal<AssistantMessage[]>([]);
  readonly draft = signal('');
  readonly loading = signal(false);
  readonly error = signal('');
  readonly retryWait = signal(0);
  private request?: Subscription;
  private retryTimer?: ReturnType<typeof setInterval>;
  private generation = 0;
  private readonly routeSubscription = this.router.events.pipe(filter((event) => event instanceof NavigationStart)).subscribe(() => this.cancelRequest());

  constructor() {
    this.content.corpus$.subscribe({ next: (value) => this.corpus.set(value), error: () => this.error.set('Help content could not be loaded. Please refresh the page.') });
  }

  useSuggestion(question: string): void { this.draft.set(question); this.submit(); }

  submit(): void {
    const question = this.draft().trim();
    if (!question || this.loading() || this.retryWait() > 0) return;
    const existingLast = this.messages().at(-1);
    const failedDraft = existingLast?.role === 'user' && !!this.error();
    const historyMessages = this.messages().map((message) => ({ role: message.role, content: message.role === 'assistant' ? message.content.slice(0, 4000) : message.content }))
      .filter((_, index) => !(failedDraft && index === this.messages().length - 1)).slice(-8);
    while (historyMessages.length && historyMessages[0].role !== 'user') historyMessages.shift();
    while (historyMessages.reduce((sum, message) => sum + message.content.length, 0) > 8000) {
      historyMessages.splice(0, historyMessages.length >= 2 ? 2 : 1);
    }
    if (failedDraft) this.messages.update((items) => [...items.slice(0, -1), { role: 'user', content: question }]);
    else this.messages.update((items) => [...items, { role: 'user', content: question }]);
    this.error.set(''); this.loading.set(true);
    const requestId = ++this.generation;
    this.request = this.assistant.ask(question, historyMessages).subscribe({
      next: (reply) => {
        if (requestId !== this.generation) return;
        const answerText = reply.paragraphs.map((paragraph) => paragraph.text).join('\n');
        this.messages.update((items) => [...items, { role: 'assistant', content: answerText, paragraphs: reply.paragraphs, citations: reply.citations, usage: reply.usage }]);
        this.draft.set(''); this.loading.set(false);
      },
      error: (failure: unknown) => {
        if (requestId !== this.generation) return;
        this.loading.set(false);
        this.error.set(describeAssistantFailure(failure));
        const wait = retryDelaySeconds(failure);
        if (wait) this.startRetryTimer(wait);
      },
    });
  }

  retry(): void {
    if (this.draft().trim()) this.submit();
    else {
      const failed = [...this.messages()].reverse().find((message) => message.role === 'user');
      if (failed) { this.draft.set(failed.content); this.submit(); }
    }
  }

  clear(): void {
    this.cancelRequest(); this.messages.set([]); this.draft.set(''); this.error.set(''); this.retryWait.set(0);
  }

  private startRetryTimer(seconds: number): void {
    clearInterval(this.retryTimer); this.retryWait.set(seconds);
    this.retryTimer = setInterval(() => {
      const remaining = this.retryWait() - 1;
      this.retryWait.set(Math.max(0, remaining));
      if (remaining <= 0) clearInterval(this.retryTimer);
    }, 1000);
  }

  private cancelRequest(): void { this.generation++; this.request?.unsubscribe(); this.loading.set(false); }
  ngOnDestroy(): void { this.cancelRequest(); this.routeSubscription.unsubscribe(); clearInterval(this.retryTimer); }
}
