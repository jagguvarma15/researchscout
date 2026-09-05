import { describe, expect, it } from 'vitest';

import { monthOf, withMonthHeaders } from './timeline';

describe('monthOf', () => {
  it('formats an ISO date as its month and year in UTC', () => {
    expect(monthOf('2026-03-15')).toBe('March 2026');
  });

  it('reads a date just past the UTC day boundary without slipping a month', () => {
    expect(monthOf('2026-04-01T00:30:00Z')).toBe('April 2026');
  });

  it('labels a null date Undated', () => {
    expect(monthOf(null)).toBe('Undated');
  });
});

describe('withMonthHeaders', () => {
  it('carries a header only when the month changes from the row before', () => {
    const items = [
      { published_on: '2026-03-20' },
      { published_on: '2026-03-05' },
      { published_on: '2026-02-28' },
    ];
    expect(withMonthHeaders(items).map((row) => row.monthHeader)).toEqual([
      'March 2026',
      null,
      'February 2026',
    ]);
  });

  it('the first row always carries its header', () => {
    expect(withMonthHeaders([{ published_on: '2026-01-01' }])[0].monthHeader).toBe('January 2026');
  });
});
