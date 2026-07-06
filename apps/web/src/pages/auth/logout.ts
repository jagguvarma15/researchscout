import type { APIRoute } from 'astro';

import { destroySession, oidcConfig, SESSION_COOKIE, SITE_URL } from '../../lib/auth';

export const GET: APIRoute = async ({ cookies, redirect }) => {
  const sid = cookies.get(SESSION_COOKIE)?.value;
  const session = sid ? await destroySession(sid) : null;
  cookies.delete(SESSION_COOKIE, { path: '/' });

  const endSession = oidcConfig.serverMetadata().end_session_endpoint;
  if (endSession && session?.idToken) {
    const target = new URL(endSession);
    target.searchParams.set('id_token_hint', session.idToken);
    target.searchParams.set('post_logout_redirect_uri', SITE_URL);
    return redirect(target.href, 302);
  }
  return redirect('/', 302);
};
