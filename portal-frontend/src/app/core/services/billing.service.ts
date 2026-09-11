import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';

@Injectable({ providedIn: 'root' })
export class BillingService {
  private readonly http = inject(HttpClient);

  checkout(tier: string): void {
    this.http.post<{ checkoutUrl: string }>('/api/v1/billing/checkout', { tier }).subscribe({
      next: ({ checkoutUrl }) => {
        if (checkoutUrl.startsWith('https://checkout.stripe.com/')) {
          window.location.assign(checkoutUrl);
        }
      },
      error: () => undefined,
    });
  }
}
