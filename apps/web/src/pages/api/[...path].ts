// Same-origin proxy to the API. Credentials are attached here, on the server: the caller's
// access token never reaches the browser, and the service-token headers that let this
// deployment through the API's edge policy never appear in client code either.

import type { APIRoute } from 'astro';

const API_URL = process.env.API_URL ?? 'http://localhost:8000';
const SITE_URL = process.env.SITE_URL ?? 'http://localhost:4321';
// The shared secret that gets this deployment through the API's front door. Unset locally,
// where the API is reachable directly and open.
const SERVICE_TOKEN = process.env.API_SERVICE_TOKEN ?? '';
const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

export const ALL: APIRoute = async ({ clientAddress, locals, params, request, url }) => {
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
  if (locals.accessToken) headers.set('authorization', `Bearer ${locals.accessToken}`);
  if (SERVICE_TOKEN) {
    headers.set('x-rs-service-token', SERVICE_TOKEN);
    // Every request reaches the API from this server, so without this the rate limiter would
    // put every signed-out visitor in one bucket. The API believes it only because the token
    // above proves where it came from.
    if (clientAddress) headers.set('x-rs-client-ip', clientAddress);
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
