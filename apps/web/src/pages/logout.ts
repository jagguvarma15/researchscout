// Sign out: drop the session cookie here, then end the session at the provider so the next
// sign-in actually asks who you are instead of silently reusing their cookie.

import type { APIRoute } from 'astro';

import { SESSION_COOKIE, authEnabled, logoutUrl } from '../lib/auth';

export const GET: APIRoute = ({ cookies, redirect }) => {
  cookies.delete(SESSION_COOKIE, { path: '/' });
  return redirect(authEnabled() ? logoutUrl() : '/', 302);
};
