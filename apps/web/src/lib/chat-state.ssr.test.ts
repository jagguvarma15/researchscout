// @vitest-environment node
//
// The chat module holds state at module scope, and Base.astro renders ScoutPanel on the
// server - where module scope is shared by every request in the Node process. These pin the
// isolation guarantee: evaluated without a window, the module starts empty and stays empty,
// so one visitor's transcript can never leak into another visitor's server-rendered HTML.

import { describe, expect, it } from 'vitest';

import { chat, restoreConversation } from './chat-state.svelte';

describe('chat state under SSR', () => {
  it('starts empty when the module loads without a window', () => {
    // The module-level restore call is behind a window guard; in Node it must not have run.
    expect(chat.messages).toHaveLength(0);
    expect(chat.asked).toBe(false);
    expect(chat.busy).toBe(false);
  });

  it('restoreConversation is a no-op without browser storage', () => {
    // Node 22 defines a throwing localStorage global; the guard and the try/catch both
    // have to hold for this to stay silent.
    restoreConversation();
    expect(chat.messages).toHaveLength(0);
    expect(chat.asked).toBe(false);
  });
});
