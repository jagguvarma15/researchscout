// An opaque per-account key for scoping client-side storage, server side only.
//
// The Scout transcript persists in localStorage, which outlives sign-out on a shared
// browser. Stamping the page with a stable tag derived from the session's sub lets the
// client discard another account's stored state without ever exposing the sub itself:
// twelve characters of a hash identify "same account or not" and nothing more.

import { createHash } from 'node:crypto';

/** A short stable tag for this account, or the empty string when signed out. */
export function ownerTag(sub: string | null | undefined): string {
  if (!sub) return '';
  return createHash('sha256').update(sub, 'utf8').digest('base64url').slice(0, 12);
}
