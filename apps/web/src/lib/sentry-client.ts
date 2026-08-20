// Browser error reporting: Sentry when PUBLIC_SENTRY_DSN was set at build time, nothing
// otherwise. Errors only - no tracing, no session replay - so the added bundle stays
// small and the free tier's monthly event budget goes to tracebacks. Imported once from
// the Base layout's bundled script, which every page shares; the default integrations
// hook window errors and unhandled rejections, both of which are discarded today.
import { init } from '@sentry/browser';

const dsn = import.meta.env.PUBLIC_SENTRY_DSN ?? '';

export function initErrorReporting(): void {
  if (!dsn) return;
  init({
    dsn,
    environment: import.meta.env.PROD ? 'production' : 'development',
    sendDefaultPii: false,
  });
}
