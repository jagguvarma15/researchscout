import { describe, expect, it } from 'vitest';

import { compact, exponent, humanDate, issueNumber, issueSummary, weightsLabel, whyLine } from './format';

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

describe('humanDate', () => {
  it('renders an ISO date as prose, ignoring any time part', () => {
    expect(humanDate('2026-09-01')).toBe('Sep 1, 2026');
    expect(humanDate('2026-12-31T23:00:00+00:00')).toBe('Dec 31, 2026');
  });

  it('hands back what it cannot parse', () => {
    expect(humanDate('soon')).toBe('soon');
  });
});

describe('whyLine', () => {
  it('names the top two positive signals', () => {
    expect(whyLine({ citation: 2.1, code_stars: 0.8, discussion: 0.2 })).toBe(
      'Momentum from citations and code stars'
    );
  });

  it('stays quiet when nothing contributed', () => {
    expect(whyLine({})).toBe('');
    expect(whyLine(undefined)).toBe('');
    expect(whyLine({ citation: -0.4 })).toBe('');
  });

  it('falls back to a readable name for unknown signal keys', () => {
    expect(whyLine({ lab_notes: 1.0 })).toBe('Momentum from lab notes');
  });
});

describe('issueSummary', () => {
  it('counts papers with the right plural', () => {
    expect(issueSummary(10)).toBe('10 papers');
    expect(issueSummary(1)).toBe('1 paper');
  });

  it('renders nothing at zero', () => {
    expect(issueSummary(0)).toBe('');
  });
});
