// @vitest-environment node
//
// The session cookie is the only place an API access token lives, so these pin the two
// properties that matter: what goes in comes back out, and anything else reads as signed out.
// The module reads its configuration at import time, so the environment is set first and the
// import is dynamic.
//
// Node rather than the project-wide jsdom: this module only ever runs on the server, and
// jsdom's separate Uint8Array realm makes jose reject its own buffers.

import { beforeAll, describe, expect, it } from 'vitest';

type AuthModule = typeof import('./auth');

let auth: AuthModule;

beforeAll(async () => {
  process.env.AUTH0_DOMAIN = 'example.us.auth0.com';
  process.env.AUTH0_CLIENT_ID = 'client';
  process.env.AUTH0_CLIENT_SECRET = 'secret';
  process.env.SESSION_SECRET = 'a-long-enough-development-secret';
  process.env.SITE_URL = 'https://example.test';
  auth = await import('./auth');
});

const session = {
  sub: 'auth0|abc',
  username: 'ada',
  accessToken: 'header.payload.signature',
  refreshToken: 'refresh-token',
  expiresAt: 2_000_000_000,
};

describe('session cookie', () => {
  it('round-trips every field', async () => {
    const restored = await auth.unsealSession(await auth.sealSession(session));
    expect(restored).toEqual(session);
  });

  it('reads a tampered cookie as signed out rather than throwing', async () => {
    const sealed = await auth.sealSession(session);
    const tampered = `${sealed.slice(0, -4)}AAAA`;
    expect(await auth.unsealSession(tampered)).toBeNull();
    expect(await auth.unsealSession('not-a-jwe')).toBeNull();
    expect(await auth.unsealSession('')).toBeNull();
  });

  it('does not put the token anywhere readable', async () => {
    // Encrypted, not merely signed: the access token must not be recoverable by decoding.
    const sealed = await auth.sealSession(session);
    expect(sealed).not.toContain(session.accessToken);
    expect(Buffer.from(sealed.split('.')[1] ?? '', 'base64url').toString()).not.toContain(
      'accessToken',
    );
  });
});

describe('expiry', () => {
  it('treats a token inside the refresh window as expired', () => {
    expect(auth.isExpired({ ...session, expiresAt: 1000 }, 1000 - 61)).toBe(false);
    expect(auth.isExpired({ ...session, expiresAt: 1000 }, 1000 - 59)).toBe(true);
    expect(auth.isExpired({ ...session, expiresAt: 1000 }, 2000)).toBe(true);
  });

  it('never expires a session with no stated expiry', () => {
    expect(auth.isExpired({ ...session, expiresAt: 0 }, 9_999_999)).toBe(false);
  });
});

describe('post-login redirect', () => {
  it('keeps same-site paths', () => {
    expect(auth.safeNext('/saved')).toBe('/saved');
    expect(auth.safeNext('/papers/arxiv:2401.00001?read=1')).toBe('/papers/arxiv:2401.00001?read=1');
  });

  it('refuses anything that could leave the site', () => {
    // Protocol-relative and absolute URLs are the open-redirect shapes.
    expect(auth.safeNext('//evil.example/phish')).toBe('/');
    expect(auth.safeNext('https://evil.example')).toBe('/');
    expect(auth.safeNext('javascript:alert(1)')).toBe('/');
    expect(auth.safeNext(null)).toBe('/');
    expect(auth.safeNext('')).toBe('/');
  });
});
