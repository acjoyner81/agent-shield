import { Component, computed, inject } from '@angular/core';
import { AsyncPipe } from '@angular/common';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';
import { AuthService } from './core/services/auth.service';
import { TelemetryService } from './core/services/telemetry.service';
import { NotificationService } from './core/errors/error-state.service';
import { NotificationCenter } from './shared/notification-center/notification-center.component';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, AsyncPipe, NotificationCenter],
  templateUrl: './app.html',
  styleUrl: './app.css'
})
export class App {
  readonly auth = inject(AuthService);
  private readonly telemetry = inject(TelemetryService);
  private readonly notifications = inject(NotificationService);
  readonly pageTitle = inject(Router).events.pipe(filter((event) => event instanceof NavigationEnd), map((event) => this.titleFor((event as NavigationEnd).urlAfterRedirects)));
  private titleFor(url: string): string { return url.includes('logs') ? 'Observability' : url.includes('keys') ? 'API keys & access' : url.includes('pricing') ? 'Plans & billing' : 'Overview'; }

  /**
   * Shell health, from the same `/v1/health/services` the dashboard reads.
   *
   * Three states rather than one hardcoded string, because a static "All systems
   * operational" is the failure this project keeps hitting: a screen that looks
   * healthy while nothing is. Unknown is the honest state before the first poll
   * lands, and stale says so after a poll stops succeeding.
   */
  readonly healthLabel = computed(() => {
    if (this.notifications.isStale('health')) {
      return 'Health status out of date';
    }
    const overall = this.telemetry.health()?.overall;
    if (overall === undefined) {
      return 'Checking system health…';
    }
    return overall === 'healthy' ? 'All systems operational' : 'Degraded: one or more services need attention';
  });

  readonly healthStale = computed(() => this.notifications.isStale('health'));
}
