import { HttpContext, HttpContextToken } from '@angular/common/http';

/**
 * The context token a call site uses to declare what a failure should do.
 *
 * The interceptor cannot infer this. It sees a response, not its origin, and
 * the gateway's rate limiter is an app level dependency, so a 429 arrives on the
 * dashboard's background poll exactly as it does on a clicked button. Declaring
 * intent here is the only place that distinction can exist.
 *
 * The default is `toast`, so a new call site is loud by default rather than
 * silently swallowing a refusal, which is what every call site did before
 * Spec 0013.
 */
export type SurfacePolicy = 'silent' | 'banner' | 'toast';

export const SURFACE = new HttpContextToken<SurfacePolicy>(() => 'toast');

/** Background poll: a failure marks the widget stale and interrupts nobody. */
export const SILENT_POLL = new HttpContext().set(SURFACE, 'silent');

/** A user initiated action: the failure is about what they just did. */
export const USER_ACTION = new HttpContext().set(SURFACE, 'banner');
