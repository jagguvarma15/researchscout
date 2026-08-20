// Same-origin proxy to the API. Credentials are attached here, on the server: the caller's
// access token never reaches the browser, and the service-token headers that let this
// deployment through the API's edge policy never appear in client code either.

import type { APIRoute } from 'astro';

import { gateEnabled } from '../../lib/gate';
import { captureError } from '../../lib/sentry-server';
import { SITE_URL } from '../../lib/site-url.js';

const API_URL = process.env.API_URL ?? 'http://localhost:8000';

// The shared secret that gets this deployment through the API's front door. Unset locally,
// where the API is reachable directly and open.
const SERVICE_TOKEN = process.env.API_SERVICE_TOKEN ?? '';
const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

export const ALL: APIRoute = async ({ clientAddress, locals, params, request, url }) => {
  // Belt to the middleware's suspenders: the proxy is the one route whose compromise
  // exposes the backend, so it refuses unapproved callers independently.
  if (gateEnabled() && !(locals.user && locals.approved)) {
    return Response.json({ detail: 'sign in required' }, { status: 401 });
  }

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
    // above proves where it came from - sent without one it is ignored by design (a header
    // anyone can set is a fresh bucket for the asking; see api/ratelimit.py), so the nesting
    // here mirrors the API's trust model rather than hiding a bug.
    if (clientAddress) headers.set('x-rs-client-ip', clientAddress);
  }

  let response: Response;
  try {
    response = await fetch(`${API_URL}/v1/${params.path}${url.search}`, {
      method: request.method,
      headers,
      body: request.body,
      // Node's fetch requires half-duplex when streaming a request body through.
      duplex: 'half',
    } as RequestInit);
  } catch (error) {
    // A connection failure to the API used to throw out of the route as a bare 500;
    // a 502 is the honest status, and the report says which deployment fell over.
    await captureError(error);
    return Response.json({ detail: 'the API is unreachable' }, { status: 502 });
  }

  const out = new Headers();
  // content-type so bodies parse; retry-after because the chat's 429 copy tells the reader
  // how long to wait and can only be honest if the number survives the proxy; the caching
  // and buffering directives because the API set them for a reason (SSE streams die behind
  // a proxy that buffers them).
  for (const name of ['content-type', 'retry-after', 'cache-control', 'x-accel-buffering']) {
    const value = response.headers.get(name);
    if (value) out.set(name, value);
  }
  return new Response(response.body, { status: response.status, headers: out });
};
