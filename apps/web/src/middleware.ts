import { defineMiddleware } from 'astro:middleware';

import {
  SESSION_COOKIE,
  authEnabled,
  isExpired,
  refreshSession,
  sealSession,
  unsealSession,
} from './lib/auth';

// Who the request is acting as. With sign-in configured that is whoever the session cookie
// says, or nobody; without it the site keeps its single built-in local user, so a local
// install behaves exactly as it did before accounts existed.
const LOCAL_USER = { sub: 'local', username: 'local' };

const THIRTY_DAYS = 60 * 60 * 24 * 30;

export const onRequest = defineMiddleware(async (context, next) => {
  if (!authEnabled()) {
    context.locals.user = LOCAL_USER;
    context.locals.accessToken = null;
    return next();
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
  return next();
});
