import { describe, expect, it } from 'vitest';

import { pageLink, pageNumbers } from './paging';

describe('pageNumbers', () => {
  it('marks gaps with null and always keeps both endpoints', () => {
    expect(pageNumbers(7, 20)).toEqual([1, null, 6, 7, 8, null, 20]);
  });

  it('runs contiguously when the window touches the endpoints', () => {
    expect(pageNumbers(2, 4)).toEqual([1, 2, 3, 4]);
  });

  it('shows a lone page without gaps', () => {
    expect(pageNumbers(1, 1)).toEqual([1]);
  });

  it('windows the start without a leading gap', () => {
    expect(pageNumbers(1, 10)).toEqual([1, 2, null, 10]);
  });

  it('windows the end without a trailing gap', () => {
    expect(pageNumbers(10, 10)).toEqual([1, null, 9, 10]);
  });

  it('collapses a one-step gap into the number itself', () => {
    // 1 .. 3 4 5 .. 20 would hide page 2 behind an ellipsis standing for one page;
    // the set arithmetic instead emits the gap only when it spans more than one.
    expect(pageNumbers(3, 20)).toEqual([1, 2, 3, 4, null, 20]);
  });
});

describe('pageLink', () => {
  it('keeps every existing filter and swaps only the page', () => {
    const search = new URLSearchParams('q=llm&days=7&page=2');
    expect(pageLink(search, 3)).toBe('/?q=llm&days=7&page=3');
  });

  it('adds the page param when absent', () => {
    expect(pageLink(new URLSearchParams('sort=citations'), 2)).toBe('/?sort=citations&page=2');
  });

  it('does not mutate the params it was given', () => {
    const search = new URLSearchParams('page=1');
    pageLink(search, 9);
    expect(search.get('page')).toBe('1');
  });

  it('links under another base path', () => {
    expect(pageLink(new URLSearchParams('org=openai'), 2, '/models')).toBe(
      '/models?org=openai&page=2'
    );
  });
});
