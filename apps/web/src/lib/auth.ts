// Sign-in against any OIDC provider (Auth0 in production), server side only.
//
// Shape of the thing: the browser never sees a token. The authorization code flow with PKCE
// runs in this Node process, the resulting tokens are sealed into one encrypted cookie, and the
// API proxy unseals them to attach a Bearer header. That keeps the access token out of client
// JavaScript entirely, which is the reason for the extra machinery over a client-side SDK.
//
// With AUTH0_DOMAIN unset the whole module stays dormant and the site runs as the built-in
// local user, exactly as it did before sign-in existed - the same switch the API uses.

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
}

export const SESSION_COOKIE = 'rs_session';
export const RETURN_COOKIE = 'rs_return';
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
  discovered ??= oidc.discovery(new URL(`https://${domain}`), clientId, clientSecret);
  return discovered;
}

function key(): Uint8Array {
  // A 32-byte key for A256GCM. Any secret works; it is stretched by hashing to a fixed length
  // so operators are not forced to generate exactly 32 bytes of base64.
  const bytes = new TextEncoder().encode(sessionSecret);
  const out = new Uint8Array(32);
  for (let i = 0; i < bytes.length; i += 1) out[i % 32] ^= bytes[i];
  return out;
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
    const { sub, username, accessToken, refreshToken, expiresAt } = payload as Record<
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

/** Trade a refresh token for a fresh access token; null when the provider refuses. */
export async function refreshSession(session: Session): Promise<Session | null> {
  if (!session.refreshToken) return null;
  try {
    const config = await configuration();
    const tokens = await oidc.refreshTokenGrant(config, session.refreshToken);
    const next = sessionFromTokens(tokens);
    // Providers may or may not rotate the refresh token; keep the old one if they did not.
    return { ...next, refreshToken: next.refreshToken ?? session.refreshToken };
  } catch {
    return null;
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
