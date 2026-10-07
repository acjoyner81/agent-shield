import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';

import { DashboardComponent } from './dashboard.component';
import {
  AUTO_REFRESH_INTERVAL_MS,
  ServiceHealthResponse,
  TelemetryService,
  UsageSummary,
} from '../../core/services/telemetry.service';
import { NotificationService } from '../../core/errors/error-state.service';

const USAGE_ADMIN: UsageSummary = {
  tenant_id: 'tenant_alpha',
  period_start: '2026-09-01',
  period_end: '2026-09-30',
  totals: {
    input_tokens: 1000,
    output_tokens: 3000,
    total_tokens: 4000,
    total_requests: 8,
    quality_passed: 6,
    failed_requests: 2,
    rate_limited_requests: 3,
    estimated_cost_usd: 0.01,
  },
  by_model: [
    {
      model: 'gpt-4o',
      input_tokens: 1000,
      output_tokens: 3000,
      total_tokens: 4000,
      request_count: 8,
      cost_usd: 0.01,
    },
  ],
};

const USAGE_NO_BILLING: UsageSummary = {
  ...USAGE_ADMIN,
  totals: { ...USAGE_ADMIN.totals, estimated_cost_usd: null },
  by_model: [{ ...USAGE_ADMIN.by_model[0], cost_usd: null }],
};

const HEALTH: ServiceHealthResponse = {
  overall: 'healthy',
  services: [
    { name: 'gateway', status: 'healthy', latency_ms: 0.4 },
    { name: 'mcp-server', status: 'healthy', latency_ms: 18 },
    { name: 'redis', status: 'degraded', latency_ms: 2500 },
  ],
};

describe('Dashboard refresh contract (Spec 0011 AC-5, AC-6)', () => {
  let httpMock: HttpTestingController;
  let service: TelemetryService;

  /** The two polled endpoints (Spec 0011 AC-5). */
  const flushPoll = (usage: UsageSummary, health: ServiceHealthResponse = HEALTH) => {
    const usageReq = httpMock.expectOne('/api/v1/usage/summary');
    expect(usageReq.request.method).toBe('GET');
    usageReq.flush(usage);

    const healthReq = httpMock.expectOne('/api/v1/health/services');
    expect(healthReq.request.method).toBe('GET');
    healthReq.flush(health);
  };

  /** Component init also pulls the recent log rows for the table. */
  const flushInit = (usage: UsageSummary, health: ServiceHealthResponse = HEALTH) => {
    httpMock.expectOne('/api/v1/telemetry/logs').flush([]);
    flushPoll(usage, health);
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [DashboardComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    httpMock = TestBed.inject(HttpTestingController);
    service = TestBed.inject(TelemetryService);
  });

  afterEach(() => {
    service.stopAutoRefresh();
    httpMock.verify();
  });

  it('polls usage and health on init', () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();

    flushInit(USAGE_ADMIN);
    expect(service.usage()).toEqual(USAGE_ADMIN);
    expect(service.health()).toEqual(HEALTH);
  });

  it('polls both endpoints every 60 seconds', fakeAsync(() => {
    expect(AUTO_REFRESH_INTERVAL_MS).toBe(60000);

    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    flushInit(USAGE_ADMIN);

    // Not yet due
    tick(AUTO_REFRESH_INTERVAL_MS - 1);
    httpMock.expectNone('/api/v1/usage/summary');

    // Due: one poll per endpoint
    tick(1);
    const second = { ...USAGE_ADMIN, totals: { ...USAGE_ADMIN.totals, total_tokens: 8000 } };
    flushPoll(second);
    expect(service.usage()?.totals.total_tokens).toBe(8000);

    // And again on the next tick
    tick(AUTO_REFRESH_INTERVAL_MS);
    flushPoll(USAGE_ADMIN);

    // Stop the poll inside the fake zone, otherwise the pending periodic task
    // outlives the test and leaks two unmatched requests into httpMock.verify().
    service.stopAutoRefresh();
    expect(service.usage()).toEqual(USAGE_ADMIN);
  }));

  it('refetches on a manual refresh', () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    flushInit(USAGE_ADMIN);

    const fresh = { ...USAGE_ADMIN, totals: { ...USAGE_ADMIN.totals, total_tokens: 12345 } };
    service.refreshDashboard();
    flushPoll(fresh);

    expect(service.usage()?.totals.total_tokens).toBe(12345);
  });

  it('updates both signals reactively from one refresh', () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    flushInit(USAGE_ADMIN);

    service.refreshDashboard();
    flushPoll(USAGE_ADMIN, { ...HEALTH, overall: 'degraded' });

    expect(service.health()?.overall).toBe('degraded');
  });

  it('stops polling when the dashboard is destroyed', fakeAsync(() => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    flushInit(USAGE_ADMIN);

    fixture.destroy();
    tick(AUTO_REFRESH_INTERVAL_MS * 2);

    httpMock.expectNone('/api/v1/usage/summary');
    httpMock.expectNone('/api/v1/health/services');
  }));

  it('derives cost, quality, and failure values from the summary', () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    flushInit(USAGE_ADMIN);

    expect(service.canSeeCost()).toBeTrue();
    expect(service.estimatedCostUsd()).toBe(0.01);
    expect(service.periodPassRate()).toBe(75); // 6 of 8 requests
    expect(service.periodFailures()).toBe(2);
    expect(service.periodRateLimited()).toBe(3);
  });

  it('hides cost but keeps usage when the principal lacks billing:admin', () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    flushInit(USAGE_NO_BILLING);

    expect(service.canSeeCost()).toBeFalse();
    expect(service.estimatedCostUsd()).toBeNull();
    expect(service.periodFailures()).toBe(2);
    expect(service.usage()?.totals.total_tokens).toBe(4000);
  });

  it('renders the spend card as hidden rather than zero for a non admin', () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    flushInit(USAGE_NO_BILLING);

    const el = fixture.nativeElement as HTMLElement;
    const cards = Array.from(el.querySelectorAll('.metric-card'));
    const spendCard = cards.find((c) => c.textContent?.includes('Estimated spend'));
    expect(spendCard?.textContent).toContain('billing:admin required');
    expect(spendCard?.textContent).not.toContain('0.00');
  });

  it('lists each probed service with its status and latency', () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    flushInit(USAGE_ADMIN, HEALTH);
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    const rows = el.querySelectorAll('.health-list > div');
    expect(rows.length).toBe(3);
    expect(el.querySelector('.health-list')?.textContent).toContain('gateway');
    expect(el.querySelector('.health-list')?.textContent).toContain('mcp-server');
    expect(el.querySelector('.health-list')?.textContent).toContain('redis');
    expect(el.querySelector('.health-list')?.textContent).toContain('18ms');
  });

  it('shows why a degraded service failed', () => {
    const withReason: ServiceHealthResponse = {
      overall: 'degraded',
      services: [
        { name: 'gateway', status: 'healthy', latency_ms: 0.4, detail: null },
        {
          name: 'file-integrity',
          status: 'degraded',
          latency_ms: 0.2,
          detail: '5 of 112 monitored objects differ from the approved baseline',
        },
      ],
    };
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    flushInit(USAGE_ADMIN, withReason);
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.health-detail')?.textContent).toContain(
      '5 of 112 monitored objects differ',
    );
  });

  it('does not invent a reason for a healthy service', () => {
    const healthyWithDetail: ServiceHealthResponse = {
      overall: 'healthy',
      services: [{ name: 'gateway', status: 'healthy', latency_ms: 0.4, detail: 'all clear' }],
    };
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    flushInit(USAGE_ADMIN, healthyWithDetail);
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('.health-detail')).toBeNull();
  });

  it('omits the reason row when a degraded probe reported none', () => {
    const degradedNoReason: ServiceHealthResponse = {
      overall: 'degraded',
      services: [{ name: 'redis', status: 'degraded', latency_ms: 2500 }],
    };
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    flushInit(USAGE_ADMIN, degradedNoReason);
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.health-list')?.textContent).toContain('degraded');
    expect(el.querySelector('.health-detail')).toBeNull();
  });

  it('surfaces a degraded overall status', () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    flushInit(USAGE_ADMIN, { ...HEALTH, overall: 'degraded' });
    fixture.detectChanges();

    const footer = (fixture.nativeElement as HTMLElement).querySelector('.health-footer');
    expect(footer?.textContent).toContain('Degraded');
  });

  it('keeps the last good values when a poll fails', () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    flushInit(USAGE_ADMIN);

    service.refreshDashboard();
    httpMock
      .expectOne('/api/v1/usage/summary')
      .flush('boom', { status: 500, statusText: 'Server Error' });
    httpMock.expectOne('/api/v1/health/services').flush(HEALTH);

    expect(service.usage()).toEqual(USAGE_ADMIN);
  });

  /**
   * The staleness notes, which the runtime verification found missing.
   *
   * Keeping the last known figures is only honest if something says they are
   * old. The shell says so for health, but these two widgets had a stale flag
   * tracked in the store and no marker bound to it, so the page read as current
   * while showing numbers from a poll that had stopped succeeding.
   *
   * Driven through the store rather than a failed request, because these specs
   * run without the interceptor; the interceptor's own mapping is asserted in
   * `gateway-error.interceptor.spec.ts`.
   */
  describe('staleness notes', () => {
    let notifications: NotificationService;

    beforeEach(() => {
      notifications = TestBed.inject(NotificationService);
    });

    afterEach(() => notifications.ngOnDestroy());

    function staleNoteTexts(root: HTMLElement): string[] {
      return [...root.querySelectorAll('.stale-note')].map((el) => el.textContent ?? '');
    }

    it('says the usage figures are old once the usage poll stops succeeding', () => {
      const fixture = TestBed.createComponent(DashboardComponent);
      fixture.detectChanges();
      flushInit(USAGE_ADMIN);

      notifications.markFresh('usage');
      notifications.markStale('usage');
      fixture.detectChanges();

      const root = fixture.nativeElement as HTMLElement;
      expect(staleNoteTexts(root).join(' ')).toContain('last updated');
    });

    it('says the health list is old too, not just the shell pill', () => {
      const fixture = TestBed.createComponent(DashboardComponent);
      fixture.detectChanges();
      flushInit(USAGE_ADMIN);

      notifications.markFresh('health');
      notifications.markStale('health');
      fixture.detectChanges();

      const panel = (fixture.nativeElement as HTMLElement).querySelector('.activity-panel');
      expect(panel?.querySelector('.stale-note')?.textContent).toContain('last updated');
    });

    it('says nothing while the data is fresh', () => {
      const fixture = TestBed.createComponent(DashboardComponent);
      fixture.detectChanges();
      flushInit(USAGE_ADMIN);

      notifications.markFresh('usage');
      notifications.markFresh('health');
      fixture.detectChanges();

      expect((fixture.nativeElement as HTMLElement).querySelector('.stale-note')).toBeNull();
    });
  });
});
