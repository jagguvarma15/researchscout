import { describe, expect, it } from 'vitest';

import { applyTopicView, controlHref, resolveSort, resolveTrend } from './topics-view';

describe('resolveTrend', () => {
  it('accepts a known key', () => {
    expect(resolveTrend('rising')).toBe('rising');
  });

  it('falls back to every topic for an unknown or missing param', () => {
    expect(resolveTrend('nonsense')).toBe('');
    expect(resolveTrend(null)).toBe('');
  });
});

describe('resolveSort', () => {
  it('only honours size by name; everything else is momentum', () => {
    expect(resolveSort('size')).toBe('size');
    expect(resolveSort('score')).toBe('score');
    expect(resolveSort(null)).toBe('score');
  });
});

describe('controlHref', () => {
  it('omits the defaults so the bare path stays clean', () => {
    expect(controlHref('', 'score')).toBe('/topics');
  });

  it('carries a trend and a non-default sort', () => {
    expect(controlHref('rising', 'size')).toBe('/topics?trend=rising&sort=size');
  });
});

describe('applyTopicView', () => {
  const topics = [
    { trend: 'rising', size: 3 },
    { trend: 'fading', size: 9 },
    { trend: 'rising', size: 5 },
  ];

  it('filters by trend', () => {
    expect(applyTopicView(topics, { trend: 'rising', sort: 'score' })).toHaveLength(2);
  });

  it('reorders by size when asked, keeping the given order otherwise', () => {
    expect(applyTopicView(topics, { trend: '', sort: 'size' }).map((t) => t.size)).toEqual([9, 5, 3]);
    expect(applyTopicView(topics, { trend: '', sort: 'score' }).map((t) => t.size)).toEqual([3, 9, 5]);
  });
});
