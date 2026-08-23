import { describe, expect, it } from 'vitest';

import { compact, exponent, issueNumber, weightsLabel } from './format';

describe('compact', () => {
  it.each([
    [1.76e12, '1.8T'],
    [70e9, '70B'],
    [7e9, '7.0B'],
    [355e6, '355M'],
    [1_500, '1.5K'],
    [999, '999'],
    [0, '0'],
  ])('renders %d as %s', (value, expected) => {
    expect(compact(value)).toBe(expected);
  });

  it('renders null as the empty marker the caller chose', () => {
    expect(compact(null)).toBe('');
    expect(compact(null, 'unknown')).toBe('unknown');
  });
});

describe('exponent', () => {
  it('reduces training compute to its order of magnitude', () => {
    expect(exponent(3.14e25)).toBe('1e25');
  });

  it('renders nothing for null and non-positive values', () => {
    expect(exponent(null)).toBe('');
    expect(exponent(0)).toBe('');
    expect(exponent(-5)).toBe('');
  });
});

describe('weightsLabel', () => {
  it.each([
    [true, 'open'],
    [false, 'closed'],
    [null, 'unknown'],
  ])('renders %s as %s', (open, expected) => {
    expect(weightsLabel(open)).toBe(expected);
  });
});

describe('issueNumber', () => {
  it('reads the week number out of a weekly slug', () => {
    expect(issueNumber('2026-w16')).toBe(16);
    expect(issueNumber('2026-w05')).toBe(5);
  });

  it('returns null for daily report date slugs', () => {
    expect(issueNumber('2026-08-23')).toBeNull();
  });
});
