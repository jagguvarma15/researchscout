<script lang="ts">
  // The chat side panel: a single island, closed by default. Talks to the API through the
  // same-origin proxy (/api/chat), applies the SSE stream to per-message state, and
  // delegates rendering to ChatMessage. Pure logic (frame parsing, formatting, matching,
  // commands) lives in src/lib where it is unit-tested.

  import { MessageCircle, Send, X } from 'lucide-svelte';

  import type { Message, WebHit } from '../lib/chat-types';
  import { parseSseFrame, splitSseBuffer, type SseEvent } from '../lib/sse';
  import ChatMessage from './ChatMessage.svelte';
  import ScoutMascot from './ScoutMascot.svelte';

  let open = $state(false);
  let input = $state('');
  let busy = $state(false);
  let messages = $state<Message[]>([]);
  let scroller: HTMLElement | undefined = $state();
  let fab: HTMLButtonElement | undefined = $state();
  let inputEl: HTMLInputElement | undefined = $state();
  let drawer: HTMLElement | undefined = $state();
  let everOpened = false;

  // The "Ask Scout!" bubble shows until the drawer is opened once, then never again.
  // Storage failures (private mode) degrade to showing it each load - never a crash.
  const HINT_KEY = 'rs-scout-hint-dismissed';
  let showHint = $state(false);

  $effect(() => {
    // Decide after hydration so server rendering never touches storage.
    try {
      showHint = localStorage.getItem(HINT_KEY) !== '1';
    } catch {
      showHint = true;
    }
  });

  function openDrawer() {
    open = true;
    if (showHint) {
      showHint = false;
      try {
        localStorage.setItem(HINT_KEY, '1');
      } catch {
        // Private mode: the bubble returns next load, nothing breaks.
      }
    }
  }

  function handleWindowKey(event: KeyboardEvent) {
    // Close on Escape only while focus is inside the drawer, so the filter sidebar and
    // reader overlays (which have their own Escape handling) never double-close.
    if (event.key === 'Escape' && open && drawer?.contains(document.activeElement)) {
      open = false;
    }
  }

  $effect(() => {
    // Hand focus to the composer on open and back to the FAB on close; the guard keeps the
    // mount-time run from stealing focus on page load.
    if (open) {
      everOpened = true;
      inputEl?.focus();
    } else if (everOpened) {
      fab?.focus();
    }
  });

  $effect(() => {
    // Track streamed text plus card and web-hit arrivals so the newest content stays in view.
    void messages.map((m) => m.text + (m.results?.length ?? 0) + (m.webHits?.length ?? 0));
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  });

  function applyEvent(frame: SseEvent, current: Message) {
    const payload = frame.payload as Record<string, any>;
    if (frame.event === 'meta') {
      current.retrieved = payload.retrieved;
      if (current.phase !== 'streaming') current.phase = 'thinking';
    } else if (frame.event === 'results') {
      current.results = payload.items;
    } else if (frame.event === 'notfound') {
      current.notfound = { query: payload.query, webSearch: Boolean(payload.web_search) };
      current.text = 'No papers in your library matched this question.';
    } else if (frame.event === 'token') {
      // The first token flips to streaming whether or not meta arrived; guardrail refusals
      // skip meta entirely. Fast messages with structured results render cards, so the
      // duplicate raw text never accumulates.
      current.phase = 'streaming';
      if (!current.results) current.text += payload.delta;
    } else if (frame.event === 'done') {
      current.phase = 'done';
      current.cited = payload.cited;
      current.used = payload.used;
    } else if (frame.event === 'error') {
      current.phase = 'done';
      current.text = payload.message ?? 'Something went wrong.';
      current.error = true;
    }
  }

  async function send(event: SubmitEvent) {
    event.preventDefault();
    const question = input.trim();
    if (!question || busy) return;
    input = '';
    messages.push({ role: 'user', text: question });
    await ask(question, 'fast');
  }

  function summarize(message: Message) {
    // The on-demand LLM pass over the same question, as a fresh exchange.
    if (busy || !message.question) return;
    messages.push({ role: 'user', text: `Summarize: ${message.question}` });
    void ask(message.question, 'llm');
  }

  async function ask(question: string, mode: 'fast' | 'llm') {
    busy = true;
    const current: Message = { role: 'assistant', text: '', phase: 'searching', mode, question };
    messages.push(current);

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ question, mode }),
      });
      if (response.status === 429) {
        const wait = response.headers.get('Retry-After');
        current.text = `Slow down a little - try again in ${wait ?? 'a few'} seconds.`;
        current.error = true;
        return;
      }
      if (!response.ok || !response.body) {
        current.text = 'The research service is unavailable right now.';
        current.error = true;
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { frames, rest } = splitSseBuffer(buffer);
        buffer = rest;
        for (const piece of frames) {
          const parsed = parseSseFrame(piece);
          if (parsed) applyEvent(parsed, current);
        }
      }
    } catch {
      current.text = 'Connection lost mid-answer - try again.';
      current.error = true;
    } finally {
      current.phase = 'done';
      busy = false;
    }
  }

  async function searchWeb(message: Message) {
    const query = message.notfound?.query;
    if (!query || message.webBusy) return;
    message.webBusy = true;
    try {
      const response = await fetch(`/api/search/web?q=${encodeURIComponent(query)}`);
      if (!response.ok) throw new Error(String(response.status));
      const payload = await response.json();
      message.webHits = payload.hits;
      message.webFailed = payload.providers_failed;
    } catch {
      message.webHits = [];
      message.webFailed = ['arxiv', 's2'];
    } finally {
      message.webBusy = false;
    }
  }

  async function importHit(message: Message, hit: WebHit) {
    if (!hit.arxiv_id) return;
    message.imports = { ...(message.imports ?? {}), [hit.arxiv_id]: 'busy' };
    try {
      const response = await fetch('/api/papers/import', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ arxiv_id: hit.arxiv_id }),
      });
      if (!response.ok) throw new Error(String(response.status));
      const payload = await response.json();
      message.imports = { ...message.imports, [hit.arxiv_id]: payload.id };
    } catch {
      message.imports = { ...message.imports, [hit.arxiv_id]: 'error' };
    }
  }
</script>

<svelte:window onkeydown={handleWindowKey} />

{#if !open}
  {#if showHint}
    <span class="bubble" aria-hidden="true">Ask Scout!</span>
  {/if}
  <button class="fab" bind:this={fab} onclick={openDrawer} aria-label="Ask Scout">
    <MessageCircle size={22} aria-hidden="true" />
  </button>
{/if}

<!-- Deliberately non-modal, unlike the overlay.ts consumers: it is a side panel, the page
     stays usable behind it, so no focus trap, scroll lock, or backdrop. inert keeps the
     closed drawer out of the tab order. -->
<aside class="drawer" class:open bind:this={drawer} aria-label="Scout research chat" inert={!open}>
  <header>
    <span class="title">
      <ScoutMascot size={20} />
      <strong>Scout</strong>
    </span>
    <button class="close" onclick={() => (open = false)} aria-label="Close">
      <X size={18} aria-hidden="true" />
    </button>
  </header>

  <div class="messages" bind:this={scroller}>
    {#if messages.length === 0}
      <div class="empty">
        <ScoutMascot size={64} />
        <p class="hint">
          Ask anything about the papers on the radar - answers cite what they rely on.
        </p>
      </div>
    {/if}
    {#each messages as message}
      <ChatMessage
        {message}
        {busy}
        last={message === messages[messages.length - 1]}
        onsummarize={() => summarize(message)}
        onwebsearch={() => searchWeb(message)}
        onimport={(hit) => importHit(message, hit)}
      />
    {/each}
  </div>
  <form onsubmit={send}>
    <input
      type="text"
      placeholder="Type keywords or use /web for quick web search"
      bind:this={inputEl}
      bind:value={input}
      disabled={busy}
    />
    <button type="submit" disabled={busy || !input.trim()} aria-label="Send">
      <Send size={17} aria-hidden="true" />
    </button>
  </form>
  <p class="disclaimer">Scout can make mistakes, double check responses.</p>
</aside>

<style>
  .fab {
    position: fixed;
    right: 1.25rem;
    bottom: 1.25rem;
    z-index: 30;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 3.25rem;
    height: 3.25rem;
    border: none;
    border-radius: 999px;
    /* One of the two restrained gradient touches (with the brand mark). */
    background: var(--accent-grad, var(--accent, #c2410c));
    color: var(--accent-contrast, #fff);
    cursor: pointer;
    box-shadow: 0 2px 8px rgb(23 25 28 / 0.18);
    transition: background-color 0.15s ease;
  }
  .fab:hover {
    background: var(--accent-hover, #9a3412);
  }
  .fab:focus-visible {
    outline: 2px solid var(--accent, #c2410c);
    outline-offset: 2px;
  }
  .bubble {
    position: fixed;
    right: 4.9rem;
    bottom: 1.85rem;
    z-index: 30;
    /* Never intercepts clicks; the FAB is the dismiss control. */
    pointer-events: none;
    padding: 0.35rem 0.75rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: 999px;
    background: var(--surface, #fff);
    color: var(--ink, #17191c);
    font-size: 0.8rem;
    font-weight: 600;
    box-shadow: var(--shadow-sm, 0 1px 3px rgb(23 25 28 / 0.08));
    animation: bubble-in 0.25s ease-out 0.6s backwards;
  }
  .bubble::after {
    content: '';
    position: absolute;
    right: -0.3rem;
    top: 50%;
    width: 0.55rem;
    height: 0.55rem;
    background: var(--surface, #fff);
    border-right: 1px solid var(--line, #e6e1d5);
    border-top: 1px solid var(--line, #e6e1d5);
    transform: translateY(-50%) rotate(45deg);
  }
  @keyframes bubble-in {
    from {
      opacity: 0;
      transform: translateY(4px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
  .drawer {
    position: fixed;
    top: 0;
    right: 0;
    z-index: 20;
    height: 100dvh;
    width: min(560px, 100vw);
    display: flex;
    flex-direction: column;
    background: var(--surface, #fff);
    border-left: 1px solid var(--line, #e6e1d5);
    box-shadow: -8px 0 24px rgb(23 25 28 / 0.06);
    transform: translateX(100%);
    transition: transform 0.25s cubic-bezier(0.2, 0, 0, 1);
  }
  .drawer.open {
    transform: translateX(0);
  }
  @media (prefers-reduced-motion: reduce) {
    .drawer,
    .fab {
      transition: none;
    }
    .bubble {
      animation: none;
    }
  }
  header {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.55rem;
    padding: 1rem 1.25rem;
    border-bottom: 1px solid var(--line, #e6e1d5);
  }
  .title {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    color: var(--ink, #17191c);
  }
  .close {
    position: absolute;
    right: 1rem;
    top: 50%;
    transform: translateY(-50%);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 2rem;
    border: none;
    border-radius: 999px;
    background: none;
    cursor: pointer;
    color: var(--muted, #5d6570);
    transition: background-color 0.15s ease;
  }
  .close:hover {
    background: var(--surface-2, #f4f0e8);
    color: var(--ink, #17191c);
  }
  .close:focus-visible {
    outline: 2px solid var(--accent, #c2410c);
    outline-offset: 2px;
  }
  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 1rem 1.25rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.75rem;
    margin-top: 2.5rem;
    text-align: center;
    color: var(--ink, #17191c);
  }
  .hint {
    color: var(--muted, #5d6570);
    font-size: 0.9rem;
    max-width: 24rem;
  }
  form {
    display: flex;
    gap: 0.5rem;
    padding: 0.9rem 1.25rem 0.5rem;
    border-top: 1px solid var(--line, #e6e1d5);
  }
  input {
    flex: 1;
    min-width: 0;
    padding: 0.55rem 0.9rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: 999px;
    background: var(--surface, #fff);
    color: var(--ink, #17191c);
    font: inherit;
    font-size: 0.92rem;
  }
  input::placeholder {
    color: var(--muted, #5d6570);
  }
  input:focus-visible {
    outline: 2px solid var(--accent, #c2410c);
    outline-offset: 1px;
    border-color: var(--accent, #c2410c);
  }
  form button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2.6rem;
    height: 2.6rem;
    flex-shrink: 0;
    border: none;
    border-radius: 999px;
    background: var(--accent, #c2410c);
    color: var(--accent-contrast, #fff);
    cursor: pointer;
    transition: background-color 0.15s ease;
  }
  form button:hover:not(:disabled) {
    background: var(--accent-hover, #9a3412);
  }
  form button:focus-visible {
    outline: 2px solid var(--accent, #c2410c);
    outline-offset: 2px;
  }
  form button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .disclaimer {
    margin: 0;
    padding: 0.35rem 1.25rem 0.7rem;
    text-align: center;
    font-size: 0.72rem;
    color: var(--muted, #5d6570);
  }
</style>
