// What the drawer actually sends: history only on LLM asks (built from the thread before
// the new turns join it, skipping errored turns), agentic only under /deep, and the paper
// pin whenever the scope chip is set.

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ask, chat, clearScope, scope, setScope } from './chat-state.svelte';

let bodies: Record<string, unknown>[];

beforeEach(() => {
  bodies = [];
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    if (init?.body) bodies.push(JSON.parse(init.body as string) as Record<string, unknown>);
    return Promise.resolve(new Response(null, { status: 503 })) as Promise<Response>;
  });
  // Same dodge as chat-persist.test.ts: Node 22 ships a throwing localStorage global.
  vi.stubGlobal('localStorage', {
    getItem: () => null,
    setItem: () => undefined,
    removeItem: () => undefined,
  });
  chat.messages.length = 0;
  chat.asked = false;
  clearScope();
});

describe('request shapes', () => {
  it('fast asks send only the question and mode', async () => {
    await ask('what is new?', 'fast');
    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toEqual({ question: 'what is new?', mode: 'fast' });
  });

  it('llm asks carry the prior thread, without errored turns or the new turns', async () => {
    await ask('state space models', 'fast'); // 503s: the assistant turn records an error
    await ask('and for vision?', 'llm');
    const body = bodies[1];
    expect(body.mode).toBe('llm');
    const history = body.history as { role: string; text: string }[];
    expect(history).toEqual([{ role: 'user', text: 'state space models' }]);
  });

  it('deep asks flag agentic and label the user turn', async () => {
    await ask('map the field', 'llm', null, { deep: true });
    expect(bodies[0].agentic).toBe(true);
    expect(chat.messages[0].text).toBe('/deep map the field');
  });

  it('the paper pin rides every ask until cleared', async () => {
    setScope('arxiv:2401.00001', 'A Paper');
    expect(scope.paperId).toBe('arxiv:2401.00001');
    await ask('what does it conclude?', 'fast');
    expect(bodies[0].paper_id).toBe('arxiv:2401.00001');

    clearScope();
    await ask('unrelated question', 'fast');
    expect(bodies[1].paper_id).toBeUndefined();
  });
});
