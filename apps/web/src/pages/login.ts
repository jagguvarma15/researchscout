// Start sign-in. The PKCE verifier and state live in a short, httpOnly cookie for the length
// of the round trip; `next` remembers where the visitor was so they land back there.

import type { APIRoute } from 'astro';

import {
  PKCE_COOKIE_NAME,
  RETURN_COOKIE,
  authEnabled,
  safeNext,
  startLogin,
} from '../lib/auth';

const FIVE_MINUTES = 300;

export const GET: APIRoute = async ({ cookies, redirect, url }) => {
  if (!authEnabled()) return redirect('/', 302);

  const next = safeNext(url.searchParams.get('next'));
  const signup = url.searchParams.get('signup') === '1';

  const { authorizationUrl, pkce } = await startLogin(signup ? 'login' : undefined);
  const secure = url.protocol === 'https:';
  cookies.set(PKCE_COOKIE_NAME, pkce, {
    httpOnly: true,
    secure,
    sameSite: 'lax',
    path: '/',
    maxAge: FIVE_MINUTES,
  });
  cookies.set(RETURN_COOKIE, next, {
    httpOnly: true,
    secure,
    sameSite: 'lax',
    path: '/',
    maxAge: FIVE_MINUTES,
  });
  return redirect(authorizationUrl, 302);
};
