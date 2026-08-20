import { defineMiddleware } from 'astro:middleware';

import {
  SESSION_COOKIE,
  authEnabled,
  isExpired,
  refreshSession,
  sealSession,
  unsealSession,
} from './lib/auth';
import { captureError } from './lib/sentry-server';

// Every page render passes through here, so this is the one choke point where a thrown
// render can be reported before it becomes the default 500. Rethrown unchanged: the
// response the reader sees is exactly what it was before reporting existed.
async function rendered(next: () => Promise<Response>): Promise<Response> {
  try {
    return await next();
  } catch (error) {
    await captureError(error);
    throw error;
  }
}

// Response headers a public deployment should always send. The content policy is not here:
// Astro emits it per response from the configuration in lib/csp.js, because only it knows the
// hashes of the island scripts on each page.
const SECURITY_HEADERS: Record<string, string> = {
  'strict-transport-security': 'max-age=31536000; includeSubDomains',
  'x-content-type-options': 'nosniff',
  'x-frame-options': 'DENY',
  'referrer-policy': 'strict-origin-when-cross-origin',
  'permissions-policy': 'camera=(), microphone=(), geolocation=(), interest-cohort=()',
};

// Pages that are the same for everyone can sit in a CDN cache; anything shaped by who you are
// must not. The stale window is what keeps the site readable while the backend is asleep.
const PUBLIC_CACHE = 'public, s-maxage=300, stale-while-revalidate=86400';
const PRIVATE_CACHE = 'private, no-store';
const PERSONAL_PATHS = ['/profile', '/saved', '/for-you', '/login', '/callback', '/logout'];

// Who the request is acting as. With sign-in configured that is whoever the session cookie
// says, or nobody; without it the site keeps its single built-in local user, so a local
// install behaves exactly as it did before accounts existed.
const LOCAL_USER = { sub: 'local', username: 'local' };

const THIRTY_DAYS = 60 * 60 * 24 * 30;

// Astro appends a hash to style-src for every inline style block it renders, and by the CSP
// spec a hash makes 'unsafe-inline' inert - which blocks style *attributes*, the ones pdf.js
// writes onto every glyph of its text layer. Dropping those hashes is the one edit that lets
// the configured 'unsafe-inline' actually apply. It only widens style-src; script-src keeps
// the hashes Astro maintains, which is where the protection that matters lives.
function relaxInlineStyles(policy: string): string {
  return policy
    .split(';')
    .map((directive) => directive.trim())
    .filter(Boolean)
    .map((directive) =>
      directive.startsWith('style-src') ? "style-src 'self' 'unsafe-inline'" : directive,
    )
    .join('; ');
}

function harden(response: Response, context: { url: URL }, signedIn: boolean): Response {
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) response.headers.set(name, value);
  const policy = response.headers.get('content-security-policy');
  if (policy) response.headers.set('content-security-policy', relaxInlineStyles(policy));
  if (response.headers.has('cache-control')) return response;

  const path = context.url.pathname;
  const personal = signedIn || PERSONAL_PATHS.some((prefix) => path.startsWith(prefix));
  // The proxy carries account data and mutations either way; never let an edge hold it.
  const proxied = path.startsWith('/api/');
  response.headers.set('cache-control', personal || proxied ? PRIVATE_CACHE : PUBLIC_CACHE);
  // One cached copy per signed-out visitor is the point; make that explicit to the edge.
  if (!personal && !proxied) response.headers.set('vary', 'cookie');
  return response;
}

export const onRequest = defineMiddleware(async (context, next) => {
  if (!authEnabled()) {
    context.locals.user = LOCAL_USER;
    context.locals.accessToken = null;
    return harden(await rendered(next), context, true);
  }

  const cookie = context.cookies.get(SESSION_COOKIE)?.value;
  let session = cookie ? await unsealSession(cookie) : null;

  if (session && isExpired(session)) {
    const refreshed = await refreshSession(session);
    if (refreshed) {
      session = refreshed;
      context.cookies.set(SESSION_COOKIE, await sealSession(refreshed), {
        httpOnly: true,
        secure: context.url.protocol === 'https:',
        sameSite: 'lax',
        path: '/',
        maxAge: THIRTY_DAYS,
      });
    } else {
      // The refresh token is gone or revoked: signed out, and the stale cookie goes with it.
      session = null;
      context.cookies.delete(SESSION_COOKIE, { path: '/' });
    }
  }

  context.locals.user = session ? { sub: session.sub, username: session.username } : null;
  context.locals.accessToken = session?.accessToken ?? null;
  return harden(await rendered(next), context, session !== null);
});
