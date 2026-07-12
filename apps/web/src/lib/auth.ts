// BFF auth: OIDC against Keycloak, server-side sessions in Redis, opaque cookie.
// Tokens never reach the browser — pages and islands only ever see the rs_session id.
//
// Two Keycloak base URLs because of container networking: the browser is redirected to the
// public one, while token/JWKS calls go over the internal one. Keycloak pins the issuer
// (KC_HOSTNAME) so tokens validate identically either way.

import * as oidc from 'openid-client';
import { createClient } from 'redis';

const KEYCLOAK_PUBLIC_URL = process.env.KEYCLOAK_PUBLIC_URL ?? 'http://localhost:8080';
const KEYCLOAK_INTERNAL_URL = process.env.KEYCLOAK_INTERNAL_URL ?? KEYCLOAK_PUBLIC_URL;
const REALM = process.env.KEYCLOAK_REALM ?? 'researchscout';
const CLIENT_ID = process.env.OIDC_CLIENT_ID ?? 'web-bff';
const CLIENT_SECRET = process.env.OIDC_CLIENT_SECRET ?? 'dev-secret';
const REDIS_URL = process.env.REDIS_URL ?? 'redis://localhost:6379';

export const SITE_URL = process.env.SITE_URL ?? 'http://localhost:4321';
export const SESSION_COOKIE = 'rs_session';
export const AUTH_STASH_COOKIE = 'rs_auth';

const SESSION_TTL_SECONDS = 60 * 60 * 24 * 14;
const REFRESH_SKEW_SECONDS = 60;

const publicRealm = `${KEYCLOAK_PUBLIC_URL}/realms/${REALM}`;
const internalRealm = `${KEYCLOAK_INTERNAL_URL}/realms/${REALM}`;

export const oidcConfig = new oidc.Configuration(
  {
    issuer: publicRealm,
    authorization_endpoint: `${publicRealm}/protocol/openid-connect/auth`,
    token_endpoint: `${internalRealm}/protocol/openid-connect/token`,
    end_session_endpoint: `${publicRealm}/protocol/openid-connect/logout`,
  },
  CLIENT_ID,
  CLIENT_SECRET,
);
// Local Keycloak is plain http; TLS arrives with the ingress in the cluster.
oidc.allowInsecureRequests(oidcConfig);

export interface Session {
  sub: string;
  username: string;
  accessToken: string;
  refreshToken: string | null;
  idToken: string | null;
  expiresAt: number; // epoch seconds for accessToken
}

let redis: ReturnType<typeof createClient> | null = null;

async function store(): Promise<NonNullable<typeof redis>> {
  if (redis === null) {
    redis = createClient({ url: REDIS_URL });
    redis.on('error', () => {
      // Swallow to keep the process alive; callers see failures per operation.
    });
    await redis.connect();
  }
  return redis;
}

export async function createSession(session: Session): Promise<string> {
  const sid = crypto.randomUUID();
  await (await store()).set(`sess:${sid}`, JSON.stringify(session), { EX: SESSION_TTL_SECONDS });
  return sid;
}

export async function destroySession(sid: string): Promise<Session | null> {
  const client = await store();
  const raw = await client.getDel(`sess:${sid}`);
  return raw ? (JSON.parse(raw) as Session) : null;
}

async function save(sid: string, session: Session): Promise<void> {
  await (await store()).set(`sess:${sid}`, JSON.stringify(session), { EX: SESSION_TTL_SECONDS });
}

// Single-flight refresh per session id, so parallel requests don't race Keycloak
// (refresh tokens rotate — the second exchange of the same token would fail).
const inflight = new Map<string, Promise<Session | null>>();

async function refresh(sid: string, session: Session): Promise<Session | null> {
  if (!session.refreshToken) return null;
  try {
    const tokens = await oidc.refreshTokenGrant(oidcConfig, session.refreshToken);
    const next: Session = {
      ...session,
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token ?? session.refreshToken,
      idToken: tokens.id_token ?? session.idToken,
      expiresAt: Math.floor(Date.now() / 1000) + (tokens.expiresIn() ?? 60),
    };
    await save(sid, next);
    return next;
  } catch {
    await destroySession(sid);
    return null;
  }
}

/** Load a session, transparently refreshing the access token when it is about to expire. */
export async function getSession(sid: string): Promise<Session | null> {
  let raw: string | null;
  try {
    raw = await (await store()).get(`sess:${sid}`);
  } catch {
    return null;
  }
  if (!raw) return null;
  const session = JSON.parse(raw) as Session;
  if (session.expiresAt - REFRESH_SKEW_SECONDS > Math.floor(Date.now() / 1000)) {
    return session;
  }
  let pending = inflight.get(sid);
  if (!pending) {
    pending = refresh(sid, session).finally(() => inflight.delete(sid));
    inflight.set(sid, pending);
  }
  return pending;
}

export async function getAccessToken(sid: string): Promise<string | null> {
  const session = await getSession(sid);
  return session?.accessToken ?? null;
}
