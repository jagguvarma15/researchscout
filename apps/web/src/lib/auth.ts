// Sign-in against any OIDC provider (Auth0 in production), server side only.
//
// Shape of the thing: the browser never sees a token. The authorization code flow with PKCE
// runs in this Node process, the resulting tokens are sealed into one encrypted cookie, and the
// API proxy unseals them to attach a Bearer header. That keeps the access token out of client
// JavaScript entirely, which is the reason for the extra machinery over a client-side SDK.
//
// With AUTH0_DOMAIN unset the whole module stays dormant and the site runs as the built-in
// local user, exactly as it did before sign-in existed - the same switch the API uses.

import { createHash } from 'node:crypto';

import { EncryptJWT, jwtDecrypt } from 'jose';
import * as oidc from 'openid-client';

import { SITE_URL } from './site-url.js';

export interface SessionUser {
  sub: string;
  username: string;
}

export interface Session extends SessionUser {
  accessToken: string;
  refreshToken?: string;
  // Seconds since the epoch; the middleware refreshes shortly before this.
  expiresAt: number;
  // True once the entry code has been redeemed (the private-site gate). Absent on
  // cookies sealed before the gate existed, which unseal as not-approved.
  approved?: boolean;
}

export const SESSION_COOKIE = 'rs_session';
export const RETURN_COOKIE = 'rs_return';
export const ENTRY_COOKIE = 'rs_entry';
const PKCE_COOKIE = 'rs_pkce';
// Refresh this long before expiry so a request never races the token going stale.
const REFRESH_SKEW_SECONDS = 60;

const domain = process.env.AUTH0_DOMAIN ?? '';
const clientId = process.env.AUTH0_CLIENT_ID ?? '';
const clientSecret = process.env.AUTH0_CLIENT_SECRET ?? '';
const audience = process.env.AUTH0_AUDIENCE ?? 'api';
const sessionSecret = process.env.SESSION_SECRET ?? '';
const siteUrl = SITE_URL;

/** True when the site is configured to sign people in; false leaves the local-user behavior. */
export function authEnabled(): boolean {
  return Boolean(domain && clientId && clientSecret && sessionSecret);
}

export function callbackUrl(): string {
  return new URL('/callback', siteUrl).toString();
}

let discovered: Promise<oidc.Configuration> | null = null;

function configuration(): Promise<oidc.Configuration> {
  // Discovery is one network round trip; cache the promise for the life of the process.
  // A failure must not stick, though: caching a rejected promise would wedge sign-in until
  // the process restarts over one transient provider blip at boot.
  discovered ??= oidc.discovery(new URL(`https://${domain}`), clientId, clientSecret).catch(
    (error: unknown) => {
      discovered = null;
      throw error;
    },
  );
  return discovered;
}

function key(): Uint8Array {
  // A 32-byte key for A256GCM, derived by hashing so an operator is not forced to generate
  // exactly 32 bytes of base64.
  //
  // This used to fold the secret into 32 bytes by XOR, which the comment already described as
  // hashing and which is not the same thing: a secret shorter than 32 bytes left the tail of
  // the key as zeroes, and two different secrets could fold to the same key. SHA-256 is one
  // line and has neither property. Changing the derivation invalidates existing cookies, which
  // signs everyone out once - the same thing rotating SESSION_SECRET does.
  return new Uint8Array(createHash('sha256').update(sessionSecret, 'utf8').digest());
}

export async function sealSession(session: Session): Promise<string> {
  return await new EncryptJWT({ ...session })
    .setProtectedHeader({ alg: 'dir', enc: 'A256GCM' })
    .setIssuedAt()
    .setExpirationTime('30d')
    .encrypt(key());
}

export async function unsealSession(value: string): Promise<Session | null> {
  try {
    const { payload } = await jwtDecrypt(value, key());
    const { sub, username, accessToken, refreshToken, expiresAt, approved } = payload as Record<
      string,
      unknown
    >;
    if (typeof sub !== 'string' || typeof accessToken !== 'string') return null;
    return {
      sub,
      username: typeof username === 'string' ? username : sub,
      accessToken,
      refreshToken: typeof refreshToken === 'string' ? refreshToken : undefined,
      expiresAt: typeof expiresAt === 'number' ? expiresAt : 0,
      // Pre-gate cookies carry no claim and read as not-approved - the safe default.
      approved: typeof approved === 'boolean' ? approved : false,
    };
  } catch {
    // A cookie sealed with an older secret, or tampered with: treat as signed out.
    return null;
  }
}

export function isExpired(session: Session, now = Date.now() / 1000): boolean {
  return session.expiresAt > 0 && session.expiresAt - REFRESH_SKEW_SECONDS <= now;
}

/**
 * Where to send someone after sign-in. Only same-site paths: anything else would turn the
 * login link into an open redirect that hands a freshly signed-in visitor to another site.
 */
export function safeNext(raw: string | null | undefined): string {
  if (!raw || !raw.startsWith('/') || raw.startsWith('//')) return '/';
  return raw;
}

export interface PkceStart {
  authorizationUrl: string;
  pkce: string;
}

/** Begin sign-in: the URL to send the browser to, and the PKCE state to keep in a cookie. */
export async function startLogin(prompt?: string): Promise<PkceStart> {
  const config = await configuration();
  const codeVerifier = oidc.randomPKCECodeVerifier();
  const codeChallenge = await oidc.calculatePKCECodeChallenge(codeVerifier);
  const state = oidc.randomState();

  const parameters: Record<string, string> = {
    redirect_uri: callbackUrl(),
    scope: 'openid profile email offline_access',
    code_challenge: codeChallenge,
    code_challenge_method: 'S256',
    state,
    audience,
  };
  if (prompt) parameters.prompt = prompt;

  return {
    authorizationUrl: oidc.buildAuthorizationUrl(config, parameters).toString(),
    pkce: JSON.stringify({ codeVerifier, state }),
  };
}

/** Finish sign-in: exchange the code for tokens and build the session to seal. */
export async function completeLogin(currentUrl: URL, pkce: string): Promise<Session> {
  const { codeVerifier, state } = JSON.parse(pkce) as { codeVerifier: string; state: string };
  const config = await configuration();
  const tokens = await oidc.authorizationCodeGrant(config, currentUrl, {
    pkceCodeVerifier: codeVerifier,
    expectedState: state,
  });
  return sessionFromTokens(tokens);
}

/**
 * Merge a freshly minted session over the previous one, keeping what only the previous
 * one knows: the refresh token when the provider did not rotate it, and the entry-code
 * approval. Access tokens refresh hourly against a 30-day cookie, so anything not
 * carried here silently vanishes at the first refresh.
 */
export function carryOver(previous: Session, next: Session): Session {
  return {
    ...next,
    // A refresh grant may come back without an id_token, in which case the freshly minted
    // session has empty identity fields; the caller is still the same person.
    sub: next.sub || previous.sub,
    username: next.username || previous.username,
    refreshToken: next.refreshToken ?? previous.refreshToken,
    approved: previous.approved ?? false,
  };
}

/** Trade a refresh token for a fresh access token; null when the provider refuses. */
export async function refreshSession(session: Session): Promise<Session | null> {
  if (!session.refreshToken) return null;
  try {
    const config = await configuration();
    const tokens = await oidc.refreshTokenGrant(config, session.refreshToken);
    return carryOver(session, sessionFromTokens(tokens));
  } catch {
    return null;
  }
}

// How long a redeemed entry code waits for the sign-in half of the flow to finish.
const ENTRY_GRANT_MINUTES = 30;

/**
 * Seal the short-lived proof that the entry code was entered before sign-in, so it
 * survives the Auth0 round trip in a cookie the browser cannot forge.
 */
export async function sealEntryGrant(): Promise<string> {
  return await new EncryptJWT({ entry: true })
    .setProtectedHeader({ alg: 'dir', enc: 'A256GCM' })
    .setIssuedAt()
    .setExpirationTime(`${ENTRY_GRANT_MINUTES}m`)
    .encrypt(key());
}

export async function unsealEntryGrant(value: string | undefined): Promise<boolean> {
  if (!value) return false;
  try {
    const { payload } = await jwtDecrypt(value, key());
    // The literal claim is the whole check: session cookies seal under the same key,
    // and a pasted rs_session value must not read as a redeemed entry code.
    return payload.entry === true;
  } catch {
    return false;
  }
}

export function logoutUrl(): string {
  const url = new URL(`https://${domain}/v2/logout`);
  url.searchParams.set('client_id', clientId);
  url.searchParams.set('returnTo', siteUrl);
  return url.toString();
}

export const PKCE_COOKIE_NAME = PKCE_COOKIE;

// The grant helpers return the response plus `claims()`; both are needed here.
type TokenResponse = oidc.TokenEndpointResponse & oidc.TokenEndpointResponseHelpers;

function sessionFromTokens(tokens: TokenResponse): Session {
  const claims = tokens.claims();
  const sub = String(claims?.sub ?? '');
  const username =
    (typeof claims?.nickname === 'string' && claims.nickname) ||
    (typeof claims?.name === 'string' && claims.name) ||
    (typeof claims?.email === 'string' && claims.email) ||
    sub;
  return {
    sub,
    username,
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token,
    expiresAt: Math.floor(Date.now() / 1000) + (tokens.expires_in ?? 3600),
  };
}
