import { describe, expect, it } from 'vitest';

import { morphName } from './morph';

describe('morphName', () => {
  it('produces a css identifier from a canonical id', () => {
    expect(morphName('paper-title', 'arxiv:2401.12345')).toBe('paper-title-arxiv-2401-12345');
  });

  it('never emits characters a custom identifier cannot carry', () => {
    const name = morphName('x', 'doi:10.1000/j.weird(2024)#4');
    expect(/^[a-zA-Z0-9_-]+$/.test(name)).toBe(true);
  });
});
