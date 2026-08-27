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
  approved: true,
};

describe('session cookie', () => {
  it('round-trips every field', async () => {
    const restored = await auth.unsealSession(await auth.sealSession(session));
    expect(restored).toEqual(session);
  });

  it('reads a pre-gate cookie as not approved', async () => {
    // Cookies sealed before the approved claim existed carry no claim at all;
    // the safe default is the landing page, not the site.
    const { approved: _dropped, ...legacy } = session;
    const restored = await auth.unsealSession(await auth.sealSession(legacy as never));
    expect(restored?.approved).toBe(false);
    expect(restored?.sub).toBe(session.sub);
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

describe('token refresh carry-over', () => {
  it('keeps the approval and the unrotated refresh token', () => {
    // Access tokens refresh hourly against a 30-day cookie: anything carryOver drops
    // silently vanishes at the first refresh, which is how approval would be lost.
    const minted = { ...session, approved: undefined, refreshToken: undefined };
    const carried = auth.carryOver(session, minted);
    expect(carried.approved).toBe(true);
    expect(carried.refreshToken).toBe('refresh-token');
  });

  it('prefers a rotated refresh token and an unapproved previous stays unapproved', () => {
    const carried = auth.carryOver(
      { ...session, approved: false },
      { ...session, refreshToken: 'rotated' },
    );
    expect(carried.refreshToken).toBe('rotated');
    expect(carried.approved).toBe(false);
  });

  it('keeps the identity when a refresh grant carries no claims', () => {
    // A refresh response without an id_token mints a session with empty identity fields;
    // the caller has not changed, so the previous identity must survive.
    const carried = auth.carryOver(session, { ...session, sub: '', username: '' });
    expect(carried.sub).toBe('auth0|abc');
    expect(carried.username).toBe('ada');
  });

  it('lets a real re-minted identity win', () => {
    const carried = auth.carryOver(session, { ...session, sub: 'auth0|xyz', username: 'grace' });
    expect(carried.sub).toBe('auth0|xyz');
    expect(carried.username).toBe('grace');
  });
});

describe('entry grant', () => {
  it('round-trips and refuses tampering', async () => {
    const grant = await auth.sealEntryGrant();
    expect(await auth.unsealEntryGrant(grant)).toBe(true);
    expect(await auth.unsealEntryGrant(`${grant.slice(0, -4)}AAAA`)).toBe(false);
    expect(await auth.unsealEntryGrant('not-a-jwe')).toBe(false);
    expect(await auth.unsealEntryGrant(undefined)).toBe(false);
  });

  it('never confuses the two cookie kinds despite the shared key', async () => {
    // A pasted old session cookie must not read as a redeemed entry code, and a grant
    // must not read as a session.
    expect(await auth.unsealEntryGrant(await auth.sealSession(session))).toBe(false);
    expect(await auth.unsealSession(await auth.sealEntryGrant())).toBeNull();
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
