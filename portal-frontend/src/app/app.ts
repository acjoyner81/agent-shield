import { Component, inject } from '@angular/core';
import { AsyncPipe } from '@angular/common';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';
import { AuthService } from './core/services/auth.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, AsyncPipe],
  templateUrl: './app.html',
  styleUrl: './app.css'
})
export class App {
  readonly auth = inject(AuthService);
  readonly pageTitle = inject(Router).events.pipe(filter((event) => event instanceof NavigationEnd), map((event) => this.titleFor((event as NavigationEnd).urlAfterRedirects)));
  private titleFor(url: string): string { return url.includes('logs') ? 'Observability' : url.includes('keys') ? 'API keys & access' : url.includes('pricing') ? 'Plans & billing' : 'Overview'; }
}
