// The private-site gate: who may see what, as pure functions the middleware calls.
//
// The policy lives here rather than in middleware.ts because nothing under src/lib can
// import astro:middleware - which is exactly what makes this file testable. Env is read
// at call time (unlike auth.ts's import-time snapshot) so tests flip it per case.
//
// The gate is off unless the deployment opts in with SITE_PRIVATE=true and an
// ENTRY_CODE, and it can only work where sign-in works, so authEnabled() is part of the
// switch. Off means every function returns the permissive answer and the site behaves
// exactly as it did before the gate existed - including local dev, which never sets
// the Auth0 vars.

import { timingSafeEqual } from 'node:crypto';

import { authEnabled } from './auth';

// Reachable without a session: the landing, the legal pages Auth0's signup links to,
// the auth endpoints themselves, and the entry-code form target. Static assets never
// reach the middleware on either adapter (the standalone server tries its static
// handler first; Vercel's filesystem route precedes the render function), so hashed
// asset URLs need no entries here.
const PUBLIC_PATHS = new Set([
  '/welcome',
  '/terms',
  '/privacy',
  '/login',
  '/callback',
  '/logout',
  '/entry',
  // The Atom feed carries its own FEED_TOKEN check inside the route - feed readers
  // cannot sign in, so the session gate steps aside for exactly this path.
  '/feeds/digests.xml',
]);

export function gateEnabled(): boolean {
  return (
    process.env.SITE_PRIVATE === 'true' && Boolean(process.env.ENTRY_CODE) && authEnabled()
  );
}

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.has(pathname.replace(/\/+$/, '') || '/');
}

export type GateDecision = 'allow' | 'landing' | 'unauthorized';

/** What the middleware should do with this request while the gate is on. */
export function gateDecision(input: {
  path: string;
  signedIn: boolean;
  approved: boolean;
}): GateDecision {
  if (input.signedIn && input.approved) return 'allow';
  if (input.path.startsWith('/api/')) return 'unauthorized';
  return isPublicPath(input.path) ? 'allow' : 'landing';
}

/** Constant-time comparison against the configured entry code. */
export function codeMatches(submitted: string): boolean {
  const expected = process.env.ENTRY_CODE ?? '';
  if (!expected || !submitted) return false;
  const a = Buffer.from(submitted, 'utf8');
  const b = Buffer.from(expected, 'utf8');
  // Length is not secret; content is. Equal-length compare only, no early content exit.
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}
