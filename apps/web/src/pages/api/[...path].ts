// Same-origin proxy to the API. The browser only ever talks to this route with its session
// cookie; the cookie is swapped for a Bearer token here, so tokens never leave the server.

import type { APIRoute } from 'astro';

import { getAccessToken, SESSION_COOKIE, SITE_URL } from '../../lib/auth';

const API_URL = process.env.API_URL ?? 'http://localhost:8000';
const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

export const ALL: APIRoute = async ({ params, request, cookies, url }) => {
  if (MUTATING.has(request.method)) {
    const origin = request.headers.get('origin');
    const fetchSite = request.headers.get('sec-fetch-site');
    if ((origin && origin !== new URL(SITE_URL).origin) || fetchSite === 'cross-site') {
      return Response.json({ detail: 'cross-site request rejected' }, { status: 403 });
    }
  }

  const headers = new Headers();
  for (const name of ['accept', 'content-type']) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const sid = cookies.get(SESSION_COOKIE)?.value;
  if (sid) {
    const token = await getAccessToken(sid);
    if (token) headers.set('authorization', `Bearer ${token}`);
  }

  const response = await fetch(`${API_URL}/v1/${params.path}${url.search}`, {
    method: request.method,
    headers,
    body: request.body,
    // Node's fetch requires half-duplex when streaming a request body through.
    duplex: 'half',
  } as RequestInit);

  const out = new Headers();
  const contentType = response.headers.get('content-type');
  if (contentType) out.set('content-type', contentType);
  return new Response(response.body, { status: response.status, headers: out });
};
