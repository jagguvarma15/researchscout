// @vitest-environment node
//
// Node rather than jsdom: the module uses node:crypto and only ever runs on the server.

import { describe, expect, it } from 'vitest';

import { ownerTag } from './owner';

describe('ownerTag', () => {
  it('is stable for the same sub', () => {
    expect(ownerTag('auth0|abc')).toBe(ownerTag('auth0|abc'));
  });

  it('differs between subs', () => {
    expect(ownerTag('auth0|abc')).not.toBe(ownerTag('auth0|xyz'));
  });

  it('is empty when signed out', () => {
    expect(ownerTag(null)).toBe('');
    expect(ownerTag(undefined)).toBe('');
    expect(ownerTag('')).toBe('');
  });

  it('never contains the sub itself', () => {
    const tag = ownerTag('auth0|abc');
    expect(tag).toHaveLength(12);
    expect(tag).not.toContain('auth0');
    expect(tag).not.toContain('abc');
  });
});
