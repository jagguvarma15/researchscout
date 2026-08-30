// What the drawer does with the enriched stream: the deep-ask plan event, the provenance
// fields on done, and the error kinds - including the load-bearing rule that a failure
// arriving after text has streamed preserves the partial answer instead of replacing it.

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ask, chat, clearScope, quota, setScope } from './chat-state.svelte';

function sseResponse(frames: [string, unknown][]): Response {
  const body = frames
    .map(([event, payload]) => `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`)
    .join('');
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

function stubStream(frames: [string, unknown][]) {
  vi.stubGlobal('fetch', () => Promise.resolve(sseResponse(frames)));
}

beforeEach(() => {
  vi.stubGlobal('localStorage', {
    getItem: () => null,
    setItem: () => undefined,
    removeItem: () => undefined,
  });
  chat.messages.length = 0;
  chat.asked = false;
  quota.exhausted = false;
  clearScope();
});

describe('stream consumption', () => {
  it('keeps the plan event and the agentic flag from meta', async () => {
    stubStream([
      ['plan', { parts: ['sparse attention', 'kv cache'] }],
      ['meta', { retrieved: 2, mode: 'llm', agentic: true }],
      ['token', { delta: 'Answer.' }],
      ['done', { cited: [], hallucinated: [], used: [] }],
    ]);
    await ask('map the field', 'llm', null, { deep: true });
    const message = chat.messages[1];
    expect(message.plan).toEqual(['sparse attention', 'kv cache']);
    expect(message.agentic).toBe(true);
    expect(message.retrieved).toBe(2);
  });

  it('keeps hallucinated ids, the model, tokens, and latency from done', async () => {
    stubStream([
      ['meta', { retrieved: 1 }],
      ['token', { delta: 'See [arxiv:2401.00001].' }],
      [
        'done',
        {
          cited: ['arxiv:2401.00001'],
          hallucinated: ['arxiv:9999.99999'],
          used: [{ id: 'arxiv:2401.00001', title: 'T', score: 1 }],
          model: 'test-model',
          prompt_tokens: 321,
          completion_tokens: 45,
          elapsed_ms: 8200,
        },
      ],
    ]);
    await ask('q', 'llm');
    const message = chat.messages[1];
    expect(message.hallucinated).toEqual(['arxiv:9999.99999']);
    expect(message.model).toBe('test-model');
    expect(message.promptTokens).toBe(321);
    expect(message.completionTokens).toBe(45);
    expect(message.elapsedMs).toBe(8200);
  });

  it('a quota error names itself and raises the banner state', async () => {
    stubStream([
      ['meta', { retrieved: 1 }],
      ['error', { code: 502, kind: 'quota', message: 'AI quota exhausted for today' }],
    ]);
    await ask('q', 'llm');
    const message = chat.messages[1];
    expect(message.errorKind).toBe('quota');
    expect(message.error).toBe(true);
    expect(message.text).toContain('Quick answers still work');
    expect(quota.exhausted).toBe(true);
  });

  it('a completed generated answer lowers the banner again', async () => {
    quota.exhausted = true;
    stubStream([
      ['meta', { retrieved: 1 }],
      ['token', { delta: 'Answer.' }],
      ['done', { cited: [], hallucinated: [], used: [] }],
    ]);
    await ask('q', 'llm');
    expect(quota.exhausted).toBe(false);
  });

  it('an error after streamed text preserves the partial answer', async () => {
    stubStream([
      ['meta', { retrieved: 1 }],
      ['token', { delta: 'Four paragraphs of ' }],
      ['token', { delta: 'partial answer.' }],
      ['error', { code: 502, kind: 'unavailable', message: 'LLM backend unavailable' }],
    ]);
    await ask('q', 'llm');
    const message = chat.messages[1];
    expect(message.text).toBe('Four paragraphs of partial answer.');
    expect(message.error).toBeUndefined();
    expect(message.errorNote).toBe('LLM backend unavailable');
    expect(message.errorKind).toBe('unavailable');
  });

  it('an old API error frame still resolves a kind from its code', async () => {
    stubStream([['error', { code: 503, message: 'the answer service is busy right now' }]]);
    await ask('q', 'llm');
    const message = chat.messages[1];
    expect(message.errorKind).toBe('busy');
    expect(message.text).toContain('busy with other questions');
    expect(quota.exhausted).toBe(false);
  });

  it('stamps when the exchange happened and what it was scoped to', async () => {
    setScope('arxiv:2401.00001', 'A Paper');
    stubStream([
      ['meta', { retrieved: 1 }],
      ['token', { delta: 'Answer.' }],
      ['done', { cited: [], hallucinated: [], used: [] }],
    ]);
    const before = Date.now();
    await ask('what does it conclude?', 'llm');
    expect(chat.messages[0].at).toBeGreaterThanOrEqual(before);
    const message = chat.messages[1];
    expect(message.at).toBeGreaterThanOrEqual(before);
    expect(message.scope).toEqual({ paperId: 'arxiv:2401.00001', title: 'A Paper' });
  });
});
