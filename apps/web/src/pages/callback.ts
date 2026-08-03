// Finish sign-in: exchange the code for tokens, seal them into the session cookie, and send
// the visitor back where they started. State and PKCE are verified inside completeLogin, so a
// forged callback fails there rather than here.
//
// Nothing here redirects to /login on failure. It used to, and that turned any persistent
// problem - a wrong client secret, a provider that refuses the exchange - into an infinite
// loop: /callback fails, sends the browser to /login, the provider still has a session and
// answers immediately, /callback fails again. The browser gives up with a redirect error and
// the actual reason is never shown to anyone. A failure that stops and says what happened is
// worth more than a retry that cannot converge.

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

/** An OAuth error carries a code and a description; anything else is just a message. */
function describe(error: unknown): string {
  if (error && typeof error === 'object') {
    const record = error as Record<string, unknown>;
    const code = record.error ?? record.code;
    const detail = record.error_description ?? record.message;
    if (typeof code === 'string') {
      return typeof detail === 'string' && detail ? `${code}: ${detail}` : code;
    }
    if (typeof detail === 'string') return detail;
  }
  return String(error);
}

function failed(reason: string, status = 400): Response {
  // Deliberately plain: this runs when sign-in is broken, so it depends on nothing.
  const escaped = reason.replace(/[<>&]/g, (c) => `&#${c.charCodeAt(0)};`);
  return new Response(
    `<!doctype html><meta charset="utf-8"><title>Sign-in failed</title>
     <style>body{font:16px/1.6 system-ui,sans-serif;margin:4rem auto;max-width:34rem;padding:0 1rem}
     code{background:#f4f4f5;padding:.15rem .4rem;border-radius:4px;word-break:break-word}</style>
     <h1>Sign-in did not complete</h1>
     <p>The identity provider reported:</p>
     <p><code>${escaped}</code></p>
     <p><a href="/login">Try again</a> or <a href="/">carry on without an account</a>.</p>`,
    { status, headers: { 'content-type': 'text/html; charset=utf-8' } },
  );
}

export const GET: APIRoute = async ({ cookies, redirect, url }) => {
  if (!authEnabled()) return redirect('/', 302);

  // The provider can also refuse before any exchange: consent declined, access denied.
  const providerError = url.searchParams.get('error');
  if (providerError) {
    return failed(`${providerError}: ${url.searchParams.get('error_description') ?? ''}`.trim());
  }

  const pkce = cookies.get(PKCE_COOKIE_NAME)?.value;
  if (!pkce) {
    return failed(
      'this sign-in was started more than five minutes ago, or in a different browser',
    );
  }

  const next = safeNext(cookies.get(RETURN_COOKIE)?.value);
  cookies.delete(PKCE_COOKIE_NAME, { path: '/' });
  cookies.delete(RETURN_COOKIE, { path: '/' });

  let sealed: string;
  try {
    sealed = await sealSession(await completeLogin(url, pkce));
  } catch (error) {
    console.error('sign-in exchange failed', error);
    return failed(describe(error));
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
