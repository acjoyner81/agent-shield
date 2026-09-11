import { Routes } from '@angular/router';
import { authGuard } from './core/guards/auth.guard';

export const routes: Routes = [
	{ path: '', pathMatch: 'full', redirectTo: 'dashboard' },
	{ path: 'dashboard', canActivate: [authGuard], loadComponent: () => import('./features/dashboard/dashboard.component').then((m) => m.DashboardComponent) },
	{ path: 'logs', canActivate: [authGuard], loadComponent: () => import('./features/logs/logs.component').then((m) => m.LogsComponent) },
	{ path: 'keys', canActivate: [authGuard], loadComponent: () => import('./features/keys/keys.component').then((m) => m.KeysComponent) },
	{ path: 'pricing', loadComponent: () => import('./features/pricing/pricing.component').then((m) => m.PricingComponent) },
	{ path: '**', redirectTo: 'dashboard' },
];
