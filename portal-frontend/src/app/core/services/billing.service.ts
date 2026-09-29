import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';

import { USER_ACTION } from '../errors/surface.context';

@Injectable({ providedIn: 'root' })
export class BillingService {
  private readonly http = inject(HttpClient);

  /**
   * A click on Upgrade, so a refusal is the whole story: the user is trying to
   * spend money and the gateway said no. The old `error: () => undefined` made
   * a 402 indistinguishable from a silent success.
   */
  checkout(tier: string): void {
    this.http
      .post<{ checkoutUrl: string }>('/api/v1/billing/checkout', { tier }, { context: USER_ACTION })
      .subscribe({
        next: ({ checkoutUrl }) => {
          if (checkoutUrl.startsWith('https://checkout.stripe.com/')) {
            window.location.assign(checkoutUrl);
          }
        },
        // Present but empty on purpose. The USER_ACTION context is what makes the
        // refusal visible; the old `error: () => undefined` looked identical but
        // carried no policy, so nothing was ever reported. See keys.service.ts.
        error: () => undefined,
      });
  }
}
