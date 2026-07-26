import { defineMiddleware } from 'astro:middleware';

// The app runs local-only: every request acts as the built-in local user, matching the API's
// no-auth mode (RS_OIDC_ISSUER unset).
const LOCAL_USER = { sub: 'local', username: 'local' };

export const onRequest = defineMiddleware((context, next) => {
  context.locals.user = LOCAL_USER;
  return next();
});
