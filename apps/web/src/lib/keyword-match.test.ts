import { describe, expect, it } from 'vitest';

import type { KeywordCount } from './chat-types';
import { loaderMatches, matchKeywords } from './keyword-match';

const DICT: KeywordCount[] = [
  { keyword: 'sparse attention', papers: 12 },
  { keyword: 'attention routing', papers: 4 },
  { keyword: 'reinforcement learning', papers: 20 },
  { keyword: 'diffusion models', papers: 9 },
  { keyword: 'attention', papers: 30 },
];

describe('matchKeywords', () => {
  it('prefix-matches the word being typed and ranks by papers', () => {
    const hits = matchKeywords('sparse atten', DICT);
    expect(hits.map((h) => h.keyword)).toEqual([
      'attention',
      'sparse attention',
      'attention routing',
    ]);
  });

  it('substring-matches earlier tokens of three or more characters', () => {
    const hits = matchKeywords('best diffusion papers', DICT);
    expect(hits.map((h) => h.keyword)).toContain('diffusion models');
  });

  it('excludes keywords the input already contains verbatim', () => {
    const hits = matchKeywords('sparse attention for long context', DICT);
    expect(hits.map((h) => h.keyword)).not.toContain('sparse attention');
    expect(hits.map((h) => h.keyword)).not.toContain('attention');
  });

  it('ignores one-character trailing tokens', () => {
    expect(matchKeywords('a', DICT)).toEqual([]);
  });

  it('returns nothing for empty input', () => {
    expect(matchKeywords('', DICT)).toEqual([]);
    expect(matchKeywords('   ', DICT)).toEqual([]);
  });

  it('caps the suggestion list', () => {
    expect(matchKeywords('attention', DICT, 2)).toHaveLength(2);
  });

  it('breaks paper-count ties alphabetically', () => {
    const tied: KeywordCount[] = [
      { keyword: 'zeta models', papers: 5 },
      { keyword: 'alpha models', papers: 5 },
    ];
    expect(matchKeywords('mod', tied).map((h) => h.keyword)).toEqual([
      'alpha models',
      'zeta models',
    ]);
  });
});

describe('loaderMatches', () => {
  it('matches verbatim phrases and sums their paper counts', () => {
    const matched = loaderMatches('what is new in sparse attention?', DICT);
    expect(matched).not.toBeNull();
    expect(matched?.keywords).toContain('sparse attention');
    expect(matched?.papers).toBeGreaterThanOrEqual(12);
  });

  it('matches keywords whose every word appears as a question token', () => {
    const matched = loaderMatches('routing with attention layers', DICT);
    expect(matched?.keywords).toContain('attention routing');
  });

  it('returns null when nothing matches', () => {
    expect(loaderMatches('quantum gravity holography', DICT)).toBeNull();
  });

  it('caps and ranks by papers', () => {
    const matched = loaderMatches('sparse attention routing reinforcement learning', DICT, 2);
    expect(matched?.keywords).toEqual(['attention', 'reinforcement learning']);
  });
});
