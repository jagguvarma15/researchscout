<script lang="ts">
  // The chat side panel: a single island, closed by default. Talks to the API through the
  // same-origin proxy (/api/chat) and renders the SSE stream token by token.

  import { MessageCircle, Send, X } from 'lucide-svelte';

  interface UsedPaper {
    id: string;
    title: string;
    score: number;
  }

  interface WebHit {
    provider: string;
    title: string;
    authors: string[];
    year: number | null;
    snippet: string;
    arxiv_id: string | null;
    url: string | null;
    already_known: boolean;
    paper_id: string | null;
  }

  interface Message {
    role: 'user' | 'assistant';
    text: string;
    phase?: 'searching' | 'thinking' | 'streaming' | 'done';
    mode?: 'fast' | 'llm';
    question?: string;
    retrieved?: number;
    cited?: string[];
    used?: UsedPaper[];
    error?: boolean;
    notfound?: { query: string; webSearch: boolean };
    webBusy?: boolean;
    webHits?: WebHit[];
    webFailed?: string[];
    imports?: Record<string, string>;
  }

  let open = $state(false);
  let input = $state('');
  let busy = $state(false);
  let messages = $state<Message[]>([]);
  let scroller: HTMLElement | undefined = $state();
  let fab: HTMLButtonElement | undefined = $state();
  let inputEl: HTMLInputElement | undefined = $state();
  let everOpened = false;

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
    // Track message content so streaming tokens keep the newest text in view.
    void messages.map((m) => m.text);
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  });

  function handleFrame(frame: string, current: Message) {
    let event = 'message';
    let data = '';
    for (const line of frame.split('\n')) {
      if (line.startsWith('event: ')) event = line.slice(7).trim();
      else if (line.startsWith('data: ')) data = line.slice(6);
    }
    if (!data) return;
    const payload = JSON.parse(data);
    if (event === 'meta') {
      current.retrieved = payload.retrieved;
      if (current.phase !== 'streaming') current.phase = 'thinking';
    } else if (event === 'notfound') {
      current.notfound = { query: payload.query, webSearch: Boolean(payload.web_search) };
      current.text = 'No papers in your library matched this question.';
    } else if (event === 'token') {
      // The first token flips to streaming whether or not meta arrived; guardrail refusals
      // skip meta entirely.
      current.phase = 'streaming';
      current.text += payload.delta;
    } else if (event === 'done') {
      current.phase = 'done';
      current.cited = payload.cited;
      current.used = payload.used;
    } else if (event === 'error') {
      current.phase = 'done';
      current.text = payload.message ?? 'Something went wrong.';
      current.error = true;
    }
  }

  function statusLabel(message: Message): string | null {
    if (message.role !== 'assistant' || message.text) return null;
    if (message.phase === 'searching') return 'Searching papers';
    if (message.phase === 'thinking') {
      if (message.retrieved && message.retrieved > 0) {
        return `Reading ${message.retrieved} paper${message.retrieved === 1 ? '' : 's'}`;
      }
      return 'Thinking';
    }
    return null;
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
        current.text = `Slow down a little — try again in ${wait ?? 'a few'} seconds.`;
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
        let split;
        while ((split = buffer.indexOf('\n\n')) !== -1) {
          handleFrame(buffer.slice(0, split), current);
          buffer = buffer.slice(split + 2);
        }
      }
    } catch {
      current.text = 'Connection lost mid-answer — try again.';
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

{#if !open}
  <button class="fab" bind:this={fab} onclick={() => (open = true)} aria-label="Ask about papers">
    <MessageCircle size={22} aria-hidden="true" />
  </button>
{/if}

<aside class="drawer" class:open aria-label="Ask about research papers" aria-hidden={!open}>
  <header>
    <strong>Ask about papers</strong>
    <button class="close" onclick={() => (open = false)} aria-label="Close">
      <X size={18} aria-hidden="true" />
    </button>
  </header>

  <div class="messages" bind:this={scroller}>
    {#if messages.length === 0}
      <p class="hint">
        Ask anything about the papers on the radar — answers cite what they rely on.
      </p>
    {/if}
    {#each messages as message}
      <div class="msg {message.role}" class:error={message.error}>
        {#if statusLabel(message)}
          <p class="pending" role="status">
            {statusLabel(message)}<span class="dots" aria-hidden="true"
              ><span>.</span><span>.</span><span>.</span></span
            >
          </p>
        {:else}
          <p>{message.text}{#if message.role === 'assistant' && busy && message === messages[messages.length - 1]}<span class="cursor">▍</span>{/if}</p>
        {/if}
        {#if message.cited && message.cited.length > 0}
          <p class="citations">
            {#each message.used ?? [] as paper}
              {#if message.cited.includes(paper.id)}
                <a href={`/papers/${paper.id}`} title={paper.title}>{paper.id}</a>
              {/if}
            {/each}
          </p>
        {/if}
        {#if message.mode === 'fast' && message.phase === 'done' && !message.error && message.cited && message.cited.length > 0}
          <p class="actions">
            <button class="ghost" onclick={() => summarize(message)} disabled={busy}>
              Summarize with AI
            </button>
          </p>
        {/if}
        {#if message.notfound && message.phase === 'done'}
          <div class="webfallback">
            {#if message.notfound.webSearch && !message.webHits}
              <p class="actions">
                <button class="ghost" onclick={() => searchWeb(message)} disabled={message.webBusy}>
                  {message.webBusy ? 'Searching the web' : 'Search the web'}
                </button>
              </p>
            {/if}
            {#if message.webHits}
              {#if message.webHits.length === 0}
                <p class="webnote">Nothing found on the web either.</p>
              {/if}
              {#each message.webHits as hit}
                <div class="webhit">
                  <p class="webtitle">{hit.title}{#if hit.year}&nbsp;({hit.year}){/if}</p>
                  {#if hit.authors.length > 0}<p class="webmeta">{hit.authors.join(', ')}</p>{/if}
                  {#if hit.snippet}<p class="websnippet">{hit.snippet}</p>{/if}
                  <p class="webactions">
                    <span class="provider">{hit.provider}</span>
                    {#if hit.already_known && hit.paper_id}
                      <a href={`/papers/${hit.paper_id}`}>In library - open</a>
                    {:else if hit.arxiv_id}
                      {#if message.imports?.[hit.arxiv_id] === 'busy'}
                        <span class="webnote">Adding</span>
                      {:else if message.imports?.[hit.arxiv_id] === 'error'}
                        <span class="weberror">Could not add - try again</span>
                      {:else if message.imports?.[hit.arxiv_id]}
                        <a href={`/papers/${message.imports[hit.arxiv_id]}`}>
                          Added to Reading list - open
                        </a>
                      {:else}
                        <button class="ghost" onclick={() => importHit(message, hit)}>
                          Add to library
                        </button>
                      {/if}
                    {:else if hit.url}
                      <a href={hit.url} target="_blank" rel="noreferrer">View source</a>
                    {/if}
                  </p>
                </div>
              {/each}
              {#if message.webFailed && message.webFailed.length > 0}
                <p class="webnote">Search unavailable for: {message.webFailed.join(', ')}</p>
              {/if}
            {/if}
          </div>
        {/if}
      </div>
    {/each}
  </div>
  <form onsubmit={send}>
    <input
      type="text"
      placeholder="What's new in reinforcement learning?"
      bind:this={inputEl}
      bind:value={input}
      disabled={busy}
    />
    <button type="submit" disabled={busy || !input.trim()} aria-label="Send">
      <Send size={17} aria-hidden="true" />
    </button>
  </form>
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
  .drawer {
    position: fixed;
    top: 0;
    right: 0;
    z-index: 20;
    height: 100dvh;
    width: min(420px, 100vw);
    display: flex;
    flex-direction: column;
    background: var(--surface, #fff);
    border-left: 1px solid var(--line, #e4e7eb);
    box-shadow: -8px 0 24px rgb(23 25 28 / 0.06);
    transform: translateX(100%);
    transition: transform 0.2s ease;
  }
  .drawer.open {
    transform: translateX(0);
  }
  @media (prefers-reduced-motion: reduce) {
    .drawer,
    .fab {
      transition: none;
    }
  }
  header {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 1rem 1.25rem;
    border-bottom: 1px solid var(--line, #e4e7eb);
  }
  .close {
    margin-left: auto;
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
    background: var(--surface-2, #f5f7fa);
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
  .hint {
    color: var(--muted, #5d6570);
    font-size: 0.9rem;
  }
  .msg p {
    margin: 0;
    padding: 0.6rem 0.9rem;
    border-radius: 14px;
    font-size: 0.92rem;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .msg.user p {
    background: var(--accent, #c2410c);
    color: var(--accent-contrast, #fff);
    margin-left: 2rem;
    border-bottom-right-radius: 4px;
  }
  .msg.assistant p {
    background: var(--surface-2, #f5f7fa);
    border: 1px solid var(--line, #e4e7eb);
    margin-right: 2rem;
    border-bottom-left-radius: 4px;
  }
  .msg.error p {
    background: #fdecec;
    border-color: #f5c8c8;
    color: #8b1d1d;
  }
  .citations {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    padding: 0.4rem 0 0 !important;
    background: none !important;
    border: none !important;
  }
  .citations a {
    font-size: 0.75rem;
    font-weight: 500;
    background: var(--accent-soft, #fef3c7);
    border-radius: 999px;
    padding: 0.05rem 0.55rem;
    text-decoration: none;
    color: var(--accent-ink, #78350f);
    transition: background-color 0.15s ease;
  }
  .citations a:hover {
    background: var(--chip-hover, #fde68a);
  }
  .cursor {
    animation: blink 1s step-start infinite;
  }
  @keyframes blink {
    50% {
      opacity: 0;
    }
  }
  .msg .pending {
    color: var(--muted, #5d6570);
  }
  .actions {
    padding: 0.4rem 0 0 !important;
    background: none !important;
    border: none !important;
  }
  .ghost {
    border: 1px solid var(--line, #e4e7eb);
    border-radius: 999px;
    background: var(--surface, #fff);
    color: var(--ink, #17191c);
    font: inherit;
    font-size: 0.78rem;
    font-weight: 500;
    padding: 0.25rem 0.75rem;
    cursor: pointer;
    transition: background-color 0.15s ease;
  }
  .ghost:hover:not(:disabled) {
    background: var(--surface-2, #f5f7fa);
  }
  .ghost:disabled {
    opacity: 0.55;
    cursor: default;
  }
  .ghost:focus-visible {
    outline: 2px solid var(--accent, #c2410c);
    outline-offset: 2px;
  }
  .webfallback {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    margin-right: 2rem;
  }
  .webhit {
    border: 1px solid var(--line, #e4e7eb);
    border-radius: 10px;
    padding: 0.55rem 0.75rem;
    background: var(--surface, #fff);
  }
  .webhit p {
    margin: 0;
    padding: 0;
    background: none;
    border: none;
    border-radius: 0;
  }
  .webtitle {
    font-size: 0.85rem;
    font-weight: 600;
  }
  .webmeta {
    font-size: 0.75rem;
    color: var(--muted, #5d6570);
  }
  .websnippet {
    font-size: 0.78rem;
    margin-top: 0.25rem !important;
    color: var(--ink, #17191c);
  }
  .webactions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.4rem !important;
    font-size: 0.78rem;
  }
  .webactions a {
    color: var(--accent-ink, #78350f);
    font-weight: 500;
  }
  .provider {
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted, #5d6570);
    border: 1px solid var(--line, #e4e7eb);
    border-radius: 999px;
    padding: 0.05rem 0.45rem;
  }
  .webnote {
    font-size: 0.78rem;
    color: var(--muted, #5d6570);
    background: none !important;
    border: none !important;
    padding: 0 !important;
  }
  .weberror {
    font-size: 0.78rem;
    color: #8b1d1d;
  }
  .dots span {
    display: inline-block;
    animation: dot-fade 1.2s infinite;
  }
  .dots span:nth-child(2) {
    animation-delay: 0.2s;
  }
  .dots span:nth-child(3) {
    animation-delay: 0.4s;
  }
  @keyframes dot-fade {
    0%,
    60%,
    100% {
      opacity: 0.25;
    }
    20% {
      opacity: 1;
    }
  }
  /* Stilled to a static ellipsis under reduced motion, like the cursor. */
  @media (prefers-reduced-motion: reduce) {
    .cursor,
    .dots span {
      animation: none;
    }
  }
  form {
    display: flex;
    gap: 0.5rem;
    padding: 0.9rem 1.25rem;
    border-top: 1px solid var(--line, #e4e7eb);
  }
  input {
    flex: 1;
    min-width: 0;
    padding: 0.55rem 0.9rem;
    border: 1px solid var(--line, #e4e7eb);
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
</style>
