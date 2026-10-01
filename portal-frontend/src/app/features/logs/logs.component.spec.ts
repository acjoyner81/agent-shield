import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import type { ColDef } from 'ag-grid-community';

import { LogsComponent } from './logs.component';
import { TelemetryLog, TelemetryService } from '../../core/services/telemetry.service';

/** A completed request: everything measured. */
const REQUEST: TelemetryLog = {
  eventId: 'EVT-1a2b3c4d',
  timestamp: '2026-10-01T14:28:13.459131+00:00',
  tenantId: 'tenant_alpha',
  message: 'POST /v1/tools/execute',
  costUsd: null,
  statusCode: 200,
  latencyMs: 142,
  evalPassed: null,
  traceId: 'a54c342666ae',
};

/** A key rotation: not a request, so nothing about a response is knowable. */
const KEY_ROTATION: TelemetryLog = {
  eventId: 'EVT-9f8e7d6c',
  timestamp: '2026-10-01T14:28:52.263257+00:00',
  tenantId: 'tenant_alpha',
  message: 'API key key_16a0510f revoke (Revoked)',
  costUsd: null,
  statusCode: null,
  latencyMs: null,
  evalPassed: null,
  traceId: '',
};

describe('Observability audit stream', () => {
  let component: LogsComponent;
  let telemetry: TelemetryService;

  const col = (field: string): ColDef => {
    const def = component.columnDefs.find((c) => c.field === field);
    if (!def) {
      throw new Error(`no column defined for ${field}`);
    }
    return def;
  };

  /** ag-grid hands formatters a params bag; only `value` matters here. */
  const run = (field: string, key: 'valueFormatter' | 'cellClass', value: unknown) => {
    const fn = col(field)[key] as (params: { value: unknown }) => unknown;
    return fn({ value } as never);
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [LogsComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    component = TestBed.createComponent(LogsComponent).componentInstance;
    telemetry = TestBed.inject(TelemetryService);
  });

  describe('the Event column', () => {
    it('is rendered, so a row says what happened rather than only an id', () => {
      // The projection already produced a message; without a column it was
      // fetched on every poll and never shown.
      expect(col('message').headerName).toBe('Event');
    });
  });

  describe('a row that was never an HTTP response', () => {
    it('shows no status rather than a successful 200', () => {
      // A key revocation is not a request. Rendering it green as 200 asserted a
      // successful call that never happened.
      expect(run('statusCode', 'valueFormatter', KEY_ROTATION.statusCode)).toBe('—');
    });

    it('is not coloured as a healthy response', () => {
      expect(run('statusCode', 'cellClass', KEY_ROTATION.statusCode)).toBe('status-none');
    });

    it('shows no latency rather than 0ms', () => {
      expect(run('latencyMs', 'valueFormatter', KEY_ROTATION.latencyMs)).toBe('—');
    });

    it('shows no cost rather than $0.0000', () => {
      // It spent nothing, but $0.0000 is a figure, and a fabricated one.
      expect(run('costUsd', 'valueFormatter', KEY_ROTATION.costUsd)).toBe('—');
    });
  });

  describe('a row that was an HTTP response', () => {
    it('shows its status', () => {
      expect(run('statusCode', 'valueFormatter', 200)).toBe('200');
    });

    it('colours a rate limit drop as a failure', () => {
      expect(run('statusCode', 'cellClass', 429)).toBe('status-danger');
    });

    it('colours a server error as a failure', () => {
      expect(run('statusCode', 'cellClass', 502)).toBe('status-danger');
    });

    it('colours a success as healthy', () => {
      expect(run('statusCode', 'cellClass', 200)).toBe('status-ok');
    });

    it('shows its latency', () => {
      expect(run('latencyMs', 'valueFormatter', 142)).toBe('142ms');
    });

    it('shows its cost to four places', () => {
      expect(run('costUsd', 'valueFormatter', 0.0025)).toBe('$0.0025');
    });
  });

  describe('average latency', () => {
    it('is a dash when nothing was measured', () => {
      telemetry.logs.set([KEY_ROTATION]);
      expect(component.averageLatencyMs()).toBe('—');
    });

    it('is a dash when there are no rows at all', () => {
      expect(component.averageLatencyMs()).toBe('—');
    });

    it('averages only the rows that were actually measured', () => {
      // Counting the unmeasured rows as 0ms would drag the figure toward the
      // fastest thing the gateway does.
      telemetry.logs.set([REQUEST, { ...REQUEST, latencyMs: 58 }, KEY_ROTATION]);
      expect(component.averageLatencyMs()).toBe('100ms');
    });
  });

  describe('the summary tiles', () => {
    it('counts a rate limit drop as a failed request', () => {
      telemetry.logs.set([
        { ...REQUEST, statusCode: 429 },
        { ...REQUEST, statusCode: 502 },
        { ...REQUEST, statusCode: 200 },
        KEY_ROTATION,
      ]);
      expect(telemetry.failedRequests()).toBe(2);
    });

    it('sums only the rows that carry a cost', () => {
      telemetry.logs.set([{ ...REQUEST, costUsd: 0.0025 }, KEY_ROTATION]);
      expect(telemetry.totalSpend()).toBe(0.0025);
    });
  });
});
