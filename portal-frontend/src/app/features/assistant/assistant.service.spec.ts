import { HttpErrorResponse, HttpHeaders } from '@angular/common/http';

import { describeAssistantFailure, retryDelaySeconds } from './assistant.service';

/** Build a gateway-style error response the way HttpClient would surface it. */
function failure(status: number, detail?: string, headers?: Record<string, string>): HttpErrorResponse {
  return new HttpErrorResponse({
    status,
    statusText: 'Error',
    url: '/api/v1/help/assistant',
    error: detail === undefined ? null : { detail },
    headers: headers ? new HttpHeaders(headers) : undefined,
  });
}

describe('assistant failure messages', () => {
  it('names the missing tenant claim rather than a generic outage', () => {
    // The exact state that looked like "could not answer": a valid session whose
    // token carries no tenant claim 403s every call.
    const message = describeAssistantFailure(failure(403, 'Token missing mandatory tenant identification claim'));
    expect(message).toContain('cannot use the assistant');
    expect(message).toContain('Token missing mandatory tenant identification claim');
  });

  it('tells the user to sign in again on 401', () => {
    expect(describeAssistantFailure(failure(401, 'Invalid access token'))).toContain('Sign in again');
  });

  it('explains the provider is unavailable on 503', () => {
    expect(describeAssistantFailure(failure(503))).toContain('temporarily unavailable');
  });

  it('explains the provider rejected the request on 502', () => {
    expect(describeAssistantFailure(failure(502))).toContain('provider rejected');
  });

  it('keeps the rate limit wording and its retry hint on 429', () => {
    const message = describeAssistantFailure(failure(429));
    expect(message).toContain('rate limited');
    expect(retryDelaySeconds(failure(429, undefined, { 'Retry-After': '7' }))).toBe(7);
  });

  it('falls back to a reachability message for a non-HTTP failure', () => {
    expect(describeAssistantFailure(new Error('boom'))).toContain('could not be reached');
  });

  it('never includes the gateway detail when the body carries none', () => {
    expect(describeAssistantFailure(failure(500))).not.toContain('(');
  });
});
