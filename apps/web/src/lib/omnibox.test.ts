// The omnibox has to guess, from a few characters, whether you are looking for a paper or
// asking about one. These pin the guess - especially the cases where a long technical phrase
// must stay a lookup, which is what stops the field from throwing every search at the model.

import { afterEach, describe, expect, it, vi } from 'vitest';

import { classify, createSequencer, debounce, isKnownCommand, searchUrl } from './omnibox';

describe('classify', () => {
  it('treats a bare phrase as a lookup', () => {
    expect(classify('attention is all you need')).toBe('lookup');
    expect(classify('sparse autoencoders')).toBe('lookup');
  });

  it('treats a long technical phrase as a lookup, however long', () => {
    // The reason there is no word-count rule: this is eight words and plainly not a question.
    expect(classify('sparse mixture of experts routing for multilingual translation')).toBe(
      'lookup',
    );
  });

  it('treats a trailing question mark as a question', () => {
    expect(classify('sparse autoencoders?')).toBe('question');
  });

  it('treats an opening question word as a question', () => {
    expect(classify('how do diffusion models handle high resolution images')).toBe('question');
    expect(classify('Does chain of thought help small models')).toBe('question');
    expect(classify('compare mamba and transformers')).toBe('question');
  });

  it('reads the opening word through punctuation and case', () => {
    expect(classify('  Why, exactly, does this work')).toBe('question');
  });

  it('does not mistake a question word buried mid-phrase', () => {
    expect(classify('models that explain their reasoning')).toBe('lookup');
  });

  it('routes slash commands to the command path', () => {
    expect(classify('/web mamba')).toBe('command');
    expect(classify('/nonsense')).toBe('command');
  });

  it('calls an empty field a lookup', () => {
    expect(classify('')).toBe('lookup');
    expect(classify('   ')).toBe('lookup');
  });
});

describe('isKnownCommand', () => {
  it('accepts the commands the composer implements', () => {
    expect(isKnownCommand('/web protein folding')).toBe(true);
    expect(isKnownCommand('/ai what is new')).toBe(true);
    expect(isKnownCommand('/deep map the field')).toBe(true);
  });

  it('rejects anything else, so the hint line shows instead of sending', () => {
    expect(isKnownCommand('/help')).toBe(false);
    expect(isKnownCommand('not a command')).toBe(false);
  });
});

describe('searchUrl', () => {
  it('encodes the query', () => {
    expect(searchUrl('a b&c')).toBe('/?q=a%20b%26c');
  });

  it('falls back to the bare feed when there is nothing to search for', () => {
    expect(searchUrl('   ')).toBe('/');
  });
});

describe('createSequencer', () => {
  it('holds only the newest ticket', () => {
    const seq = createSequencer();
    const first = seq.next();
    const second = seq.next();
    // The slow reply to "tran" must not overwrite the fast reply to "transformer".
    expect(seq.isCurrent(first)).toBe(false);
    expect(seq.isCurrent(second)).toBe(true);
  });
});

describe('debounce', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('fires once for a burst, on the trailing edge', () => {
    vi.useFakeTimers();
    const spy = vi.fn();
    const d = debounce(spy, 250);
    d.run();
    d.run();
    d.run();
    expect(spy).not.toHaveBeenCalled();
    vi.advanceTimersByTime(250);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('does not fire after a cancel', () => {
    vi.useFakeTimers();
    const spy = vi.fn();
    const d = debounce(spy, 250);
    d.run();
    d.cancel();
    vi.advanceTimersByTime(1000);
    expect(spy).not.toHaveBeenCalled();
  });
});
