import { Component, inject } from '@angular/core';
import { BillingService } from '../../core/services/billing.service';
import { CommonModule } from '@angular/common';

type PlanTier = 'starter' | 'pro' | 'enterprise';

@Component({
  selector: 'app-pricing',
  standalone: true,
  imports: [CommonModule],
  template: `
    <section class="pricing-head">
      <p class="eyebrow">Plans / billing</p>
      <h1 class="pricing-title">Scale with confidence.</h1>
      <p class="pricing-subtitle">Every AgentShield plan includes secure routing, spend visibility, and the guardrails your customers expect.</p>
    </section>

    <section class="pricing-grid" role="radiogroup" aria-label="Choose a plan">
      <article class="price-card" [class.selected]="selectedPlan === 'starter'" (click)="selectPlan('starter')">
        <label class="plan-choice" for="starter-plan">
          <input
            class="plan-radio"
            id="starter-plan"
            type="radio"
            name="plan"
            value="starter"
            aria-label="Select Starter plan"
            [checked]="selectedPlan === 'starter'"
          />
          <span class="plan-details">
            <span class="eyebrow">Starter</span>
            <span class="plan-price">$29<span>/mo</span></span>
            <span class="plan-description">For teams shipping their first reliable AI workflow.</span>
            <ul class="plan-features">
              <li>$10 included token spend</li>
              <li>20 requests / minute</li>
              <li>Model fallback routing</li>
            </ul>
          </span>
        </label>
        <button
          class="button button-secondary"
          type="button"
          [disabled]="selectedPlan !== 'starter'"
          (click)="subscribe('starter')"
        >
          Subscribe to Starter →
        </button>
      </article>

      <article class="price-card featured" [class.selected]="selectedPlan === 'pro'" (click)="selectPlan('pro')">
        <span class="popular">Most popular</span>
        <label class="plan-choice" for="pro-plan">
          <input
            class="plan-radio"
            id="pro-plan"
            type="radio"
            name="plan"
            value="pro"
            aria-label="Select Pro plan"
            [checked]="selectedPlan === 'pro'"
          />
          <span class="plan-details">
            <span class="eyebrow">Pro SME</span>
            <span class="plan-price">$149<span>/mo</span></span>
            <span class="plan-description">For growing teams that need control without the platform overhead.</span>
            <ul class="plan-features">
              <li>$75 included token spend</li>
              <li>100 requests / minute</li>
              <li>Semantic caching</li>
              <li>FastMCP tool integration</li>
            </ul>
          </span>
        </label>
        <button
          class="button button-primary"
          type="button"
          [disabled]="selectedPlan !== 'pro'"
          (click)="subscribe('pro')"
        >
          Subscribe to Pro →
        </button>
      </article>

      <article class="price-card" [class.selected]="selectedPlan === 'enterprise'" (click)="selectPlan('enterprise')">
        <label class="plan-choice" for="enterprise-plan">
          <input
            class="plan-radio"
            id="enterprise-plan"
            type="radio"
            name="plan"
            value="enterprise"
            aria-label="Select Enterprise plan"
            [checked]="selectedPlan === 'enterprise'"
          />
          <span class="plan-details">
            <span class="eyebrow">Enterprise</span>
            <span class="plan-price">$499<span>/mo</span></span>
            <span class="plan-description">For regulated workloads and high-volume agent operations.</span>
            <ul class="plan-features">
              <li>Metered usage allowance</li>
              <li>1,000 requests / minute</li>
              <li>LLM quality gates</li>
              <li>Dedicated Java MCP services</li>
            </ul>
          </span>
        </label>
        <button
          class="button button-secondary"
          type="button"
          [disabled]="selectedPlan !== 'enterprise'"
          (click)="subscribe('enterprise')"
        >
          Talk to sales →
        </button>
      </article>
    </section>

    <p class="plan-selection-status" role="status" aria-live="polite">
      {{ selectedPlan ? selectedPlanLabel + ' selected. Choose the button below it to continue.' : 'Select a plan to continue.' }}
    </p>
  `,
})
export class PricingComponent {
  readonly billing = inject(BillingService);
  selectedPlan: PlanTier = 'pro';

  get selectedPlanLabel(): string {
    return this.selectedPlan
      ? this.selectedPlan.charAt(0).toUpperCase() + this.selectedPlan.slice(1)
      : '';
  }

  selectPlan(tier: PlanTier): void {
    this.selectedPlan = tier;
  }

  subscribe(tier: PlanTier): void {
    if (this.selectedPlan === tier) {
      this.billing.checkout(tier);
    }
  }
}
