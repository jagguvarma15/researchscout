import { describe, expect, it } from 'vitest';

import { windowHref } from './frontier';

describe('windowHref', () => {
  const now = Date.parse('2026-09-04T12:00:00Z');

  it('windows the feed back to the build day', () => {
    expect(windowHref('2026-08-25T12:00:00Z', now)).toBe('/?days=10');
  });

  it('clamps a same-day build up to one day', () => {
    expect(windowHref('2026-09-04T11:00:00Z', now)).toBe('/?days=1');
  });

  it('clamps a very old build down to a year', () => {
    expect(windowHref('2020-01-01T00:00:00Z', now)).toBe('/?days=365');
  });
});
