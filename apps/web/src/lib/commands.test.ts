import { describe, expect, it } from 'vitest';

import { commandHint, parseInput } from './commands';

describe('parseInput', () => {
  it('treats plain text as a question', () => {
    expect(parseInput('  what is new?  ')).toEqual({ kind: 'question', text: 'what is new?' });
  });

  it('parses /web with its query', () => {
    expect(parseInput('/web diffusion transformers')).toEqual({
      kind: 'web',
      query: 'diffusion transformers',
    });
  });

  it('parses /ai with its question', () => {
    expect(parseInput('/ai compare mamba vs transformers')).toEqual({
      kind: 'ai',
      question: 'compare mamba vs transformers',
    });
  });

  it('is case-insensitive on the command word', () => {
    expect(parseInput('/Web x')).toEqual({ kind: 'web', query: 'x' });
  });

  it('returns empty arguments for bare commands', () => {
    expect(parseInput('/web')).toEqual({ kind: 'web', query: '' });
    expect(parseInput('/ai   ')).toEqual({ kind: 'ai', question: '' });
  });

  it('flags unknown commands', () => {
    expect(parseInput('/summarize this')).toEqual({ kind: 'unknown', command: '/summarize' });
  });
});

describe('commandHint', () => {
  it('shows the hint whenever the input starts a command', () => {
    expect(commandHint('/')).toContain('/web');
    expect(commandHint('/we')).toContain('/ai');
  });

  it('stays hidden for plain questions', () => {
    expect(commandHint('what is /web?')).toBeNull();
    expect(commandHint('')).toBeNull();
  });
});
