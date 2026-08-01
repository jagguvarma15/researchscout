// Finish sign-in: exchange the code for tokens, seal them into the session cookie, and send
// the visitor back where they started. State and PKCE are verified inside completeLogin, so a
// forged callback fails there rather than here.

import type { APIRoute } from 'astro';

import {
  PKCE_COOKIE_NAME,
  RETURN_COOKIE,
  SESSION_COOKIE,
  authEnabled,
  completeLogin,
  safeNext,
  sealSession,
} from '../lib/auth';

const THIRTY_DAYS = 60 * 60 * 24 * 30;

export const GET: APIRoute = async ({ cookies, redirect, url }) => {
  if (!authEnabled()) return redirect('/', 302);

  const pkce = cookies.get(PKCE_COOKIE_NAME)?.value;
  if (!pkce) return redirect('/login', 302);

  const next = safeNext(cookies.get(RETURN_COOKIE)?.value);
  cookies.delete(PKCE_COOKIE_NAME, { path: '/' });
  cookies.delete(RETURN_COOKIE, { path: '/' });

  let sealed: string;
  try {
    sealed = await sealSession(await completeLogin(url, pkce));
  } catch {
    // Expired code, replayed callback, mismatched state: start over rather than 500.
    return redirect('/login', 302);
  }

  cookies.set(SESSION_COOKIE, sealed, {
    httpOnly: true,
    secure: url.protocol === 'https:',
    sameSite: 'lax',
    path: '/',
    maxAge: THIRTY_DAYS,
  });
  return redirect(next, 302);
};
