// Pins the svelte $state semantics the chat drawer depends on. The drawer streams SSE
// events into a message object it retains a local reference to; these tests document why
// that reference must be created with $state (the fix) and why a plain object pushed
// into reactive state freezes the UI (the bug that shipped as a permanently stuck
// loader). Templates and $derived read state inside tracked effects, so the tests
// observe through $effect - untracked reads fall back to the raw target and would mask
// the bug.

import { flushSync } from 'svelte';
import { describe, expect, it } from 'vitest';

interface Entry {
  phase: string;
  text: string;
  results?: string[];
}

function observe<T>(read: () => T, act: () => void): T[] {
  const seen: T[] = [];
  const cleanup = $effect.root(() => {
    $effect(() => {
      seen.push(read());
    });
  });
  flushSync();
  act();
  flushSync();
  cleanup();
  return seen;
}

describe('$state array insertion semantics', () => {
  it('never re-renders for mutations made through a retained raw reference', () => {
    const raw: Entry = { phase: 'searching', text: '' };
    const list = $state<Entry[]>([]);
    list.push(raw);

    const seen = observe(
      () => `${list[0].phase}:${list[0].results?.length ?? 'none'}`,
      () => {
        raw.phase = 'done';
        raw.results = ['arxiv:2401.00001'];
      },
    );

    // The effect ran once at 'searching' and was never scheduled again: frozen UI.
    expect(seen).toEqual(['searching:none']);
    // And the tracked read seeded cached sources, so the mutation stays invisible.
    expect(list[0].phase).toBe('searching');
    expect(list[0].results).toBeUndefined();
  });

  it('re-renders every mutation when the retained reference is created with $state', () => {
    const live: Entry = $state({ phase: 'searching', text: '' });
    const list = $state<Entry[]>([]);
    list.push(live);
    // Identity holds: push stores the proxy itself, so the last-message check works.
    expect(list[0]).toBe(live);

    const seen = observe(
      () => `${list[0].phase}:${list[0].results?.length ?? 'none'}`,
      () => {
        live.phase = 'done';
        live.results = ['arxiv:2401.00001'];
      },
    );

    expect(seen).toEqual(['searching:none', 'done:1']);
    expect(list[0].phase).toBe('done');
    expect(list[0].results).toEqual(['arxiv:2401.00001']);
  });
});
