// The entry-code form target: one shared secret unlocks the private site.
//
// Two outcomes for a correct code, depending on when it arrives. A signed-in visitor is
// re-sealed as approved on the spot; a signed-out one gets a short-lived sealed grant
// cookie that the sign-in callback redeems, so the code survives the Auth0 round trip.
// A wrong code waits half a second before answering - the code is one shared secret, so
// the damping (plus the log line) is the whole brute-force story, and the constant-time
// compare in codeMatches keeps timing out of it.

import type { APIRoute } from 'astro';

import {
  ENTRY_COOKIE,
  SESSION_COOKIE,
  sealEntryGrant,
  sealSession,
  unsealSession,
} from '../lib/auth';
import { codeMatches, gateEnabled } from '../lib/gate';

const THIRTY_DAYS = 60 * 60 * 24 * 30;
const GRANT_SECONDS = 60 * 30;
const WRONG_CODE_DELAY_MS = 500;

function noStore(response: Response): Response {
  response.headers.set('cache-control', 'private, no-store');
  return response;
}

export const POST: APIRoute = async ({ cookies, redirect, request, url }) => {
  if (!gateEnabled()) return new Response(null, { status: 404 });

  const form = await request.formData();
  const code = String(form.get('code') ?? '').trim();

  if (!codeMatches(code)) {
    console.warn('entry code rejected');
    await new Promise((resolve) => setTimeout(resolve, WRONG_CODE_DELAY_MS));
    return noStore(redirect('/welcome?code=bad', 302));
  }

  const sealed = cookies.get(SESSION_COOKIE)?.value;
  const session = sealed ? await unsealSession(sealed) : null;
  if (session) {
    cookies.set(SESSION_COOKIE, await sealSession({ ...session, approved: true }), {
      httpOnly: true,
      secure: url.protocol === 'https:',
      sameSite: 'lax',
      path: '/',
      maxAge: THIRTY_DAYS,
    });
    return noStore(redirect('/', 302));
  }

  cookies.set(ENTRY_COOKIE, await sealEntryGrant(), {
    httpOnly: true,
    secure: url.protocol === 'https:',
    sameSite: 'lax',
    path: '/',
    maxAge: GRANT_SECONDS,
  });
  return noStore(redirect('/welcome?code=ok', 302));
};

export const GET: APIRoute = ({ redirect }) => redirect('/welcome', 302);
