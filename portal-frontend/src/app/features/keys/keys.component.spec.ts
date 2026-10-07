import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';

import { KeysComponent } from './keys.component';
import { NotificationService } from '../../core/errors/error-state.service';

/**
 * The "Protection status" panel must not claim a control the gateway lacks.
 *
 * The panel is customer facing, so every row is a statement about what the
 * gateway actually does to a request. One of them was wrong: PII redaction
 * rendered a green dot and "Enabled" while `redact_pii` and
 * `sanitize_llm_response` exist in `config/guardrails/scanner.py` and are never
 * called from the request path. A security product that tells a customer its
 * data is being scrubbed when it is not is worse than showing nothing.
 *
 * These assertions are the tripwire. Re-enabling the row is correct only once
 * `main.py` calls the helpers, and it should be a deliberate change to this file
 * rather than a quiet edit to the template.
 */
describe('KeysComponent protection status claims', () => {
  let httpMock: HttpTestingController;
  let element: HTMLElement;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [KeysComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    httpMock = TestBed.inject(HttpTestingController);

    const fixture = TestBed.createComponent(KeysComponent);
    fixture.detectChanges();
    element = fixture.nativeElement as HTMLElement;

    // ngOnInit lists the tenant's keys.
    httpMock.expectOne('/api/v1/keys').flush([]);
  });

  afterEach(() => httpMock.verify());

  it('does not claim PII redaction is enabled', () => {
    const row = [...element.querySelectorAll('.guardrail')].find((r) =>
      r.textContent?.includes('PII redaction'),
    );

    expect(row).toBeDefined();
    expect(row!.textContent).toContain('Not applied');
    expect(row!.textContent).not.toContain('Enabled');
    // A green dot is the visual claim; it must not sit beside this row.
    expect(row!.querySelector('.green-dot')).toBeNull();
  });

  it('tells the user not to send personal data yet', () => {
    // Silence would let the row read as a minor caveat. The customer has to be
    // told what it costs them, because the rest of the page invites them to
    // build against this gateway.
    expect(element.textContent).toContain('not applied to request or response traffic');
  });

  it('still reports the guards that are genuinely wired', () => {
    // The correction must not quietly disable a real control. The injection
    // filter and the budget gate are both called from the request path.
    const enabled = [...element.querySelectorAll('.guardrail')]
      .map((r) => r.textContent ?? '')
      .filter((t) => t.includes('Enabled'));

    expect(enabled.some((t) => t.includes('Prompt injection filter'))).toBeTrue();
    expect(enabled.some((t) => t.includes('Budget enforcement'))).toBeTrue();
    expect(enabled.some((t) => t.includes('PII redaction'))).toBeFalse();
  });
});

/**
 * A failed key list load keeps the keys on screen, so the list has to say the
 * figures are old.
 *
 * The runtime verification found the note bound only on `/logs`: the store tracked
 * staleness per widget, but nothing rendered it for the keys list. A revoked key
 * still shown next to "Rotate keys every 90 days" reads as live, and on the
 * credentials page that is a security question rather than a cosmetic one.
 */
describe('KeysComponent key list staleness', () => {
  let httpMock: HttpTestingController;
  let notifications: NotificationService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [KeysComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    httpMock = TestBed.inject(HttpTestingController);
    notifications = TestBed.inject(NotificationService);
  });

  afterEach(() => {
    notifications.ngOnDestroy();
    httpMock.verify();
  });

  it('keeps the keys on screen and says they are old when the reload fails', () => {
    const fixture = TestBed.createComponent(KeysComponent);
    fixture.detectChanges();
    httpMock.expectOne('/api/v1/keys').flush([
      { key_id: 'key_abc', name: 'Production', key_prefix: 'ask_live', created_at: '2026-09-01', status: 'active' },
    ]);
    fixture.detectChanges();
    const root = fixture.nativeElement as HTMLElement;

    notifications.markFresh('keys');
    notifications.markStale('keys');
    fixture.detectChanges();

    expect(root.textContent).toContain('Production');
    expect(root.querySelector('.stale-note')?.textContent).toContain('last updated');
  });

  it('says nothing while the list is fresh', () => {
    const fixture = TestBed.createComponent(KeysComponent);
    fixture.detectChanges();
    httpMock.expectOne('/api/v1/keys').flush([]);
    notifications.markFresh('keys');
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('.stale-note')).toBeNull();
  });
});
