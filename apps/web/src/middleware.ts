import { defineMiddleware } from 'astro:middleware';

import { getSession, SESSION_COOKIE } from './lib/auth';

export const onRequest = defineMiddleware(async (context, next) => {
  context.locals.user = null;
  const sid = context.cookies.get(SESSION_COOKIE)?.value;
  if (sid) {
    const session = await getSession(sid);
    if (session) {
      context.locals.user = { sub: session.sub, username: session.username };
    } else {
      context.cookies.delete(SESSION_COOKIE, { path: '/' });
    }
  }
  return next();
});
