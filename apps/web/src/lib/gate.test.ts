// @vitest-environment node
//
// The gate policy is pure functions over env + a request shape, so every branch the
// middleware acts on is pinned here without an Astro harness. gate.ts reads env at call
// time (unlike auth.ts), so each test flips the variables directly; auth.ts snapshots at
// import, so the Auth0 vars are set before the dynamic import and never unset.

import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

type GateModule = typeof import('./gate');

let gate: GateModule;

beforeAll(async () => {
  process.env.AUTH0_DOMAIN = 'example.us.auth0.com';
  process.env.AUTH0_CLIENT_ID = 'client';
  process.env.AUTH0_CLIENT_SECRET = 'secret';
  process.env.SESSION_SECRET = 'a-long-enough-development-secret';
  process.env.SITE_URL = 'https://example.test';
  gate = await import('./gate');
});

beforeEach(() => {
  process.env.SITE_PRIVATE = 'true';
  process.env.ENTRY_CODE = 'correct-horse';
});

describe('gateEnabled', () => {
  it('requires the flag, the code, and working sign-in together', () => {
    expect(gate.gateEnabled()).toBe(true);
    process.env.SITE_PRIVATE = 'false';
    expect(gate.gateEnabled()).toBe(false);
    process.env.SITE_PRIVATE = 'true';
    delete process.env.ENTRY_CODE;
    expect(gate.gateEnabled()).toBe(false);
  });
});

describe('isPublicPath', () => {
  it('admits exactly the landing, the legal pages, the auth machinery, and the feed', () => {
    // The feed route carries its own FEED_TOKEN check; the session gate steps aside for it.
    for (const path of ['/welcome', '/terms', '/privacy', '/login', '/callback', '/logout', '/entry', '/feeds/digests.xml']) {
      expect(gate.isPublicPath(path), path).toBe(true);
    }
  });

  it('tolerates a trailing slash and nothing else', () => {
    expect(gate.isPublicPath('/welcome/')).toBe(true);
    expect(gate.isPublicPath('/')).toBe(false);
    expect(gate.isPublicPath('/papers/arxiv:2401.00001')).toBe(false);
    expect(gate.isPublicPath('/welcome/anything')).toBe(false);
    expect(gate.isPublicPath('/termsandmore')).toBe(false);
  });
});

describe('gateDecision', () => {
  it('lets an approved session through everywhere', () => {
    for (const path of ['/', '/saved', '/api/papers', '/welcome']) {
      expect(gate.gateDecision({ path, signedIn: true, approved: true })).toBe('allow');
    }
  });

  it('sends everyone else to the landing for pages and 401 for the proxy', () => {
    for (const visitor of [
      { signedIn: false, approved: false },
      { signedIn: true, approved: false },
    ]) {
      expect(gate.gateDecision({ path: '/', ...visitor })).toBe('landing');
      expect(gate.gateDecision({ path: '/papers/x', ...visitor })).toBe('landing');
      expect(gate.gateDecision({ path: '/api/papers', ...visitor })).toBe('unauthorized');
      expect(gate.gateDecision({ path: '/api/events', ...visitor })).toBe('unauthorized');
      expect(gate.gateDecision({ path: '/welcome', ...visitor })).toBe('allow');
      expect(gate.gateDecision({ path: '/terms', ...visitor })).toBe('allow');
      expect(gate.gateDecision({ path: '/login', ...visitor })).toBe('allow');
    }
  });
});

describe('codeMatches', () => {
  it('accepts only the exact code', () => {
    expect(gate.codeMatches('correct-horse')).toBe(true);
    expect(gate.codeMatches('correct-horsf')).toBe(false);
    expect(gate.codeMatches('correct')).toBe(false);
    expect(gate.codeMatches('correct-horse-battery')).toBe(false);
    expect(gate.codeMatches('')).toBe(false);
  });

  it('matches nothing when no code is configured', () => {
    delete process.env.ENTRY_CODE;
    expect(gate.codeMatches('')).toBe(false);
    expect(gate.codeMatches('anything')).toBe(false);
  });
});
