import { describe, expect, it } from 'vitest';

import { barWidth } from './bars';

describe('barWidth', () => {
  it('is a linear share of the maximum by default', () => {
    expect(barWidth(25, 100)).toBe(25);
    expect(barWidth(100, 100)).toBe(100);
  });

  it('uses a log scale when asked (compute spans many orders of magnitude)', () => {
    // log10(1e12) / log10(1e24) = 12 / 24 = 50%.
    expect(barWidth(1e12, 1e24, { log: true })).toBe(50);
  });

  it('guards null, non-positive values, and a non-positive max', () => {
    expect(barWidth(null, 100)).toBeNull();
    expect(barWidth(0, 100)).toBeNull();
    expect(barWidth(-5, 100)).toBeNull();
    expect(barWidth(5, 0)).toBeNull();
  });

  it('a log top of zero (a maximum of one) has no bar to draw', () => {
    expect(barWidth(1, 1, { log: true })).toBeNull();
  });
});
