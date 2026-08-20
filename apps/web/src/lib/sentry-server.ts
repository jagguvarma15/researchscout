// Server-side error reporting: Sentry when SENTRY_DSN is set, silence otherwise - the
// same self-gating contract as the backend's observe module. Module scope runs once per
// server process on the standalone Node adapter and once per cold start on Vercel;
// captureError flushes because a serverless runtime can freeze right after the response,
// and an unflushed queue is a dropped report.
import * as Sentry from '@sentry/node';

const dsn = process.env.SENTRY_DSN ?? '';

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.VERCEL ? 'production' : 'development',
    release: process.env.VERCEL_GIT_COMMIT_SHA || undefined,
    tracesSampleRate: 0,
    sendDefaultPii: false,
  });
}

export async function captureError(error: unknown): Promise<void> {
  if (!dsn) return;
  Sentry.captureException(error);
  try {
    await Sentry.flush(2000);
  } catch {
    // Losing a report must never break the response path.
  }
}
