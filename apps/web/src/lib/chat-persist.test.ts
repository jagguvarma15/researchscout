// The conversation's trip through localStorage. Two things are load-bearing here: the
// transcript really comes back (that is the feature), and what comes back is treated as
// untrusted input - restored `used` ids feed the validIds gate that makes ChatMessage's
// @html safe, and restored urls become hrefs, so both are whitelisted on the way in.

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ask, chat, clearConversation, persistNow, restoreConversation } from './chat-state.svelte';

const KEY = 'rs-scout-chat';

// A storage of our own rather than the environment's. Node 22 defines a `localStorage`
// global that throws unless the process was started with --localstorage-file, and it
// shadows the one jsdom provides - the same dodge highlights.test.ts documents.
let store: Map<string, string>;

function memoryStorage() {
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  };
}

function emptyInMemory() {
  chat.messages.length = 0;
  chat.asked = false;
}

function seed(envelope: unknown) {
  localStorage.setItem(KEY, JSON.stringify(envelope));
}

function envelope(messages: unknown[], savedAt = Date.now()) {
  return { v: 2, savedAt, messages };
}

beforeEach(() => {
  store = new Map();
  vi.stubGlobal('localStorage', memoryStorage());
  emptyInMemory();
  // The signed-out default: no owner tag on the document.
  delete document.documentElement.dataset.owner;
});

describe('conversation persistence', () => {
  it('round-trips a finished exchange', () => {
    chat.messages.push({ role: 'user', text: 'what is mamba?' });
    chat.messages.push({
      role: 'assistant',
      text: 'Mamba is a state-space model [arxiv:2312.00752].',
      phase: 'done',
      mode: 'llm',
      question: 'what is mamba?',
      cited: ['arxiv:2312.00752'],
      used: [{ id: 'arxiv:2312.00752', title: 'Mamba', score: 0.9 }],
    });
    persistNow();
    emptyInMemory();

    restoreConversation();
    expect(chat.messages).toHaveLength(2);
    expect(chat.asked).toBe(true);
    expect(chat.messages[1].used?.[0].id).toBe('arxiv:2312.00752');
    expect(chat.messages[1].cited).toEqual(['arxiv:2312.00752']);
  });

  it('drops the transcript after a day', () => {
    seed(envelope([{ role: 'user', text: 'old' }], Date.now() - 25 * 60 * 60 * 1000));
    restoreConversation();
    expect(chat.messages).toHaveLength(0);
    expect(chat.asked).toBe(false);
  });

  it('clamps restored state so nothing spins forever', () => {
    seed(
      envelope([
        {
          role: 'assistant',
          text: '',
          phase: 'streaming',
          webBusy: true,
          imports: { '2401.00001': 'busy' },
        },
      ]),
    );
    restoreConversation();
    const message = chat.messages[0];
    expect(message.phase).toBe('done');
    expect(message.webBusy).toBe(false);
    expect(message.imports).toEqual({ '2401.00001': 'error' });
  });

  it('refuses citation ids that could smuggle markup into the html sink', () => {
    seed(
      envelope([
        {
          role: 'assistant',
          text: 'see [arxiv:2401.00001]',
          cited: ['arxiv:2401.00001', '"><img src=x onerror=alert(1)>'],
          used: [
            { id: '"><img src=x onerror=alert(1)>', title: 'evil', score: 1 },
            { id: 'arxiv:2401.00001', title: 'fine', score: 1 },
          ],
        },
      ]),
    );
    restoreConversation();
    const message = chat.messages[0];
    expect(message.used?.map((paper) => paper.id)).toEqual(['arxiv:2401.00001']);
    expect(message.cited).toEqual(['arxiv:2401.00001']);
  });

  it('keeps only web urls on restored hits', () => {
    seed(
      envelope([
        {
          role: 'assistant',
          text: '',
          webHits: [
            { title: 'a', url: 'javascript:alert(1)' },
            { title: 'b', url: 'https://arxiv.org/abs/2401.00001' },
          ],
        },
      ]),
    );
    restoreConversation();
    const hits = chat.messages[0].webHits ?? [];
    expect(hits.map((hit) => hit.url)).toEqual([null, 'https://arxiv.org/abs/2401.00001']);
  });

  it('keeps the newest forty messages', () => {
    for (let i = 0; i < 50; i += 1) {
      chat.messages.push({ role: 'user', text: `q${i}` });
    }
    persistNow();
    emptyInMemory();

    restoreConversation();
    expect(chat.messages).toHaveLength(40);
    expect(chat.messages[0].text).toBe('q10');
    expect(chat.messages[39].text).toBe('q49');
  });

  it('restores nothing from a malformed or foreign envelope', () => {
    localStorage.setItem(KEY, 'not json');
    restoreConversation();
    expect(chat.messages).toHaveLength(0);

    seed({ v: 99, savedAt: Date.now(), messages: [{ role: 'user', text: 'x' }] });
    restoreConversation();
    expect(chat.messages).toHaveLength(0);

    seed({ v: 2, savedAt: Date.now(), messages: [{ role: 'wizard', text: 'x' }] });
    restoreConversation();
    expect(chat.messages).toHaveLength(0);
  });

  it('drops a pre-owner envelope', () => {
    // v1 predates the owner tag, so on a shared browser it could belong to anyone.
    seed({ v: 1, savedAt: Date.now(), messages: [{ role: 'user', text: 'x' }] });
    restoreConversation();
    expect(chat.messages).toHaveLength(0);
  });

  it('round-trips under the signed-in account that wrote it', () => {
    document.documentElement.dataset.owner = 'tag-a';
    chat.messages.push({ role: 'user', text: 'mine' });
    persistNow();
    emptyInMemory();

    restoreConversation();
    expect(chat.messages).toHaveLength(1);
    expect(chat.messages[0].text).toBe('mine');
  });

  it('removes a different account transcript instead of restoring it', () => {
    document.documentElement.dataset.owner = 'tag-a';
    chat.messages.push({ role: 'user', text: 'account a asked this' });
    persistNow();
    emptyInMemory();

    document.documentElement.dataset.owner = 'tag-b';
    restoreConversation();
    expect(chat.messages).toHaveLength(0);
    // Removed, not merely skipped: it must not linger for whoever signs in next.
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('treats a signed-out visitor as a different owner than any account', () => {
    document.documentElement.dataset.owner = 'tag-a';
    chat.messages.push({ role: 'user', text: 'account a asked this' });
    persistNow();
    emptyInMemory();

    delete document.documentElement.dataset.owner;
    restoreConversation();
    expect(chat.messages).toHaveLength(0);
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('clear removes both the thread and the stored copy', () => {
    chat.messages.push({ role: 'user', text: 'q' });
    persistNow();
    expect(localStorage.getItem(KEY)).not.toBeNull();

    clearConversation();
    expect(chat.messages).toHaveLength(0);
    expect(chat.asked).toBe(false);
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('stores nothing when the thread is empty', () => {
    persistNow();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('caps the live thread, not only the stored copy', async () => {
    // The omnibox never unmounts, so without an in-memory cap a long session grows the
    // array for the life of the tab while the persisted copy stays at forty.
    vi.stubGlobal(
      'fetch',
      () => Promise.resolve(new Response(null, { status: 503 })) as Promise<Response>,
    );
    for (let i = 0; i < 25; i += 1) {
      await ask(`q${i}`, 'fast');
    }
    expect(chat.messages.length).toBeLessThanOrEqual(40);
    expect(chat.messages[chat.messages.length - 2].text).toBe('q24');
  });
});
