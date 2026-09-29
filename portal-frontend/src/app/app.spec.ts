import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { signal } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { App } from './app';
import { AuthService } from './core/services/auth.service';
import { NotificationService } from './core/errors/error-state.service';

describe('App', () => {
  const mockAuthService = {
    userName: signal('Anthony Joyner'),
    tenantName: signal('AgentShield Enterprise'),
    authenticated: signal(true),
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter([]),
        // The shell now reads live health through TelemetryService, so it needs
        // an HttpClient. Nothing here issues a request; the shell only reads
        // signals that start empty.
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: AuthService, useValue: mockAuthService },
      ],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });

  it('should render the brand mark and workspace name', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.brand')?.textContent).toContain('AgentShield');
    expect(compiled.querySelector('.workspace-switch strong')?.textContent).toContain('AgentShield Enterprise');
  });

  describe('the shell health line', () => {
    it('does not claim health before the first poll lands', () => {
      const fixture = TestBed.createComponent(App);
      fixture.detectChanges();
      const shell = fixture.nativeElement as HTMLElement;
      // The honest unknown state, not "All systems operational".
      expect(shell.querySelector('.status-line')?.textContent).toContain('Checking system health');
      expect(shell.textContent).not.toContain('All systems operational');
    });

    it('reports a degraded platform when the health poll says so', async () => {
      const telemetry = TestBed.inject(
        (await import('./core/services/telemetry.service')).TelemetryService,
      );
      telemetry.health.set({ services: [], overall: 'degraded' });

      const fixture = TestBed.createComponent(App);
      fixture.detectChanges();
      const shell = fixture.nativeElement as HTMLElement;
      expect(shell.querySelector('.status-line')?.textContent).toContain('Degraded');
    });

    it('says the status is out of date once a health poll stops succeeding', () => {
      const notifications = TestBed.inject(NotificationService);
      notifications.markStale('health');

      const fixture = TestBed.createComponent(App);
      fixture.detectChanges();
      const shell = fixture.nativeElement as HTMLElement;
      expect(shell.querySelector('.status-line')?.textContent).toContain('out of date');
      expect(shell.querySelector('.status-line--stale')).not.toBeNull();
    });
  });
});
