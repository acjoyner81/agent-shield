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
      .post<any>('/api/v1/billing/checkout', { tier }, { context: USER_ACTION })
      .subscribe({
        next: (res) => {
          if (res?.action === 'checkout' && res?.checkoutUrl?.startsWith('https://checkout.stripe.com/')) {
            window.location.assign(res.checkoutUrl);
            return;
          }
          if (res?.action === 'updated') {
            alert('Plan updated to ' + (res.tier || tier));
            return;
          }
          if (res?.action === 'unchanged') {
            alert('Plan unchanged (' + (res.tier || tier) + ')');
            return;
          }
          if (res?.checkoutUrl?.startsWith('https://checkout.stripe.com/')) {
            window.location.assign(res.checkoutUrl);
          }
        },
        error: () => undefined,
      });
  }
}
