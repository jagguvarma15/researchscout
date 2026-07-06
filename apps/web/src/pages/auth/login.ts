import type { APIRoute } from 'astro';
import * as oidc from 'openid-client';

import { AUTH_STASH_COOKIE, oidcConfig, SITE_URL } from '../../lib/auth';

export const GET: APIRoute = async ({ cookies, redirect, url }) => {
  const verifier = oidc.randomPKCECodeVerifier();
  const challenge = await oidc.calculatePKCECodeChallenge(verifier);
  const state = oidc.randomState();
  const returnTo = url.searchParams.get('returnTo') ?? '/';

  cookies.set(
    AUTH_STASH_COOKIE,
    JSON.stringify({ verifier, state, returnTo: returnTo.startsWith('/') ? returnTo : '/' }),
    { httpOnly: true, sameSite: 'lax', path: '/', maxAge: 600 },
  );

  const authUrl = oidc.buildAuthorizationUrl(oidcConfig, {
    redirect_uri: `${SITE_URL}/auth/callback`,
    scope: 'openid profile email',
    code_challenge: challenge,
    code_challenge_method: 'S256',
    state,
  });
  return redirect(authUrl.href, 302);
};
