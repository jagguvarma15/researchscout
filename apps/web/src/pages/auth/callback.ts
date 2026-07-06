import type { APIRoute } from 'astro';
import * as oidc from 'openid-client';

import {
  AUTH_STASH_COOKIE,
  createSession,
  oidcConfig,
  SESSION_COOKIE,
  SITE_URL,
} from '../../lib/auth';

export const GET: APIRoute = async ({ cookies, redirect, url }) => {
  const raw = cookies.get(AUTH_STASH_COOKIE)?.value;
  if (!raw) return redirect('/auth/login', 302);
  const stash = JSON.parse(raw) as { verifier: string; state: string; returnTo: string };
  cookies.delete(AUTH_STASH_COOKIE, { path: '/' });

  // Rebuild the callback URL on the canonical site origin: behind the container the request
  // host can differ (0.0.0.0), which would break openid-client's redirect_uri check.
  const currentUrl = new URL(`${url.pathname}${url.search}`, SITE_URL);

  let tokens: oidc.TokenEndpointResponse & oidc.TokenEndpointResponseHelpers;
  try {
    tokens = await oidc.authorizationCodeGrant(oidcConfig, currentUrl, {
      pkceCodeVerifier: stash.verifier,
      expectedState: stash.state,
    });
  } catch {
    return redirect('/auth/login', 302);
  }

  const claims = tokens.claims();
  if (!claims) return redirect('/auth/login', 302);

  const sid = await createSession({
    sub: claims.sub,
    username: (claims.preferred_username as string | undefined) ?? claims.sub,
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token ?? null,
    idToken: tokens.id_token ?? null,
    expiresAt: Math.floor(Date.now() / 1000) + (tokens.expiresIn() ?? 60),
  });
  cookies.set(SESSION_COOKIE, sid, {
    httpOnly: true,
    sameSite: 'lax',
    secure: SITE_URL.startsWith('https://'),
    path: '/',
    maxAge: 60 * 60 * 24 * 14,
  });
  return redirect(stash.returnTo, 302);
};
