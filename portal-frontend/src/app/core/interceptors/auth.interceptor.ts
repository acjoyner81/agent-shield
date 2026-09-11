import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { AuthService } from '@auth0/auth0-angular';
import { catchError, of, switchMap, take } from 'rxjs';

export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const auth = inject(AuthService);

  return auth.getAccessTokenSilently().pipe(
    take(1),
    catchError(() => of(null)),
    switchMap((token) => token
      ? next(request.clone({ setHeaders: { Authorization: `Bearer ${token}` } }))
      : next(request)),
  );
};
