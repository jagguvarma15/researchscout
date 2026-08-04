<script lang="ts">
  // The Scout half of the omnibox panel: the conversation, and the transport behind it.
  //
  // There is no composer here - the omnibox field above is the composer, and it drives this
  // through the exported ask / search / stop functions. Everything else is the chat drawer's
  // behaviour carried over intact: SSE frames applied to per-message state, fast-mode result
  // cards, the web-search fallback, one-click import, and the Stop button. The pure pieces
  // (frame splitting, formatting, keyword matching) still live in src/lib where they are
  // unit-tested; rendering is still ChatMessage's job.

  import type { KeywordCount, Message, WebHit } from '../lib/chat-types';
  import { loaderMatches } from '../lib/keyword-match';
  import { parseSseFrame, splitSseBuffer, type SseEvent } from '../lib/sse';
  import ChatMessage from './ChatMessage.svelte';

  let {
    dictionary,
    onbusy,
    onactivity,
  }: {
    dictionary: KeywordCount[] | null;
    onbusy: (busy: boolean) => void;
    onactivity: () => void;
  } = $props();

  let messages = $state<Message[]>([]);
  let busy = $state(false);
  let controller: AbortController | null = null;

  $effect(() => {
    // Track streamed text plus card and web-hit arrivals so the panel keeps the newest
    // content in view; the panel owns the scroll container, so it does the scrolling.
    void messages.map((m) => m.text + (m.results?.length ?? 0) + (m.webHits?.length ?? 0));
    onactivity();
  });

  function setBusy(value: boolean) {
    busy = value;
    onbusy(value);
  }

  export function isBusy(): boolean {
    return busy;
  }

  export function stop() {
    controller?.abort();
  }

  export function reset() {
    stop();
    messages = [];
  }

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

  export async function ask(question: string, mode: 'fast' | 'llm') {
    if (busy) return;
    messages.push({ role: 'user', text: mode === 'llm' ? `/ai ${question}` : question });
    setBusy(true);
    controller = new AbortController();
    // The message MUST be created with $state: a plain object pushed into a $state array
    // gets proxied on insert, and mutations through the retained raw reference fire no
    // signals and are invisible on later proxy reads (svelte caches per-property sources
    // on first read). $state here makes this local reference the live proxy itself.
    const current: Message = $state({
      role: 'assistant',
      text: '',
      phase: 'searching',
      mode,
      question,
    });
    if (mode === 'fast' && dictionary) {
      // The loader line names the dictionary keywords the question hit.
      current.matched = loaderMatches(question, dictionary);
    }
    messages.push(current);

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ question, mode }),
        signal: controller.signal,
      });
      if (response.status === 401) {
        // Generated answers need an account; the quick ones do not. Say which, and offer
        // the way in, rather than reporting this as a failure.
        current.text = 'Generated answers need an account.';
        current.needsSignIn = true;
        return;
      }
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
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        // The Stop button: keep whatever streamed and mark the message, no error styling.
        current.stopped = true;
      } else {
        current.text = 'Connection lost mid-answer - try again.';
        current.error = true;
      }
    } finally {
      current.phase = 'done';
      setBusy(false);
      controller = null;
    }
  }

  export async function runWebSearch(query: string) {
    // The /web command: an assistant message holding web hit cards, reusing the same
    // fallback rendering and one-click import flow as the notfound path.
    if (busy) return;
    messages.push({ role: 'user', text: `/web ${query}` });
    setBusy(true);
    controller = new AbortController();
    // $state for the same reason as in ask(): the retained reference must be the proxy.
    const current: Message = $state({
      role: 'assistant',
      text: '',
      phase: 'searching',
      question: query,
      webBusy: true,
    });
    messages.push(current);
    try {
      const response = await fetch(`/api/search/web?q=${encodeURIComponent(query)}`, {
        signal: controller.signal,
      });
      if (response.status === 401) {
        current.text = 'Searching the web needs an account.';
        current.needsSignIn = true;
        return;
      }
      if (response.status === 404) {
        current.text = 'Web search is disabled.';
        current.error = true;
        return;
      }
      if (response.status === 429) {
        current.text = 'Slow down a little - try again in a few seconds.';
        current.error = true;
        return;
      }
      if (!response.ok) throw new Error(String(response.status));
      const payload = await response.json();
      current.webHits = payload.hits;
      current.webFailed = payload.providers_failed;
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        current.stopped = true;
      } else {
        current.webHits = [];
        current.webFailed = ['arxiv', 's2'];
      }
    } finally {
      current.webBusy = false;
      current.phase = 'done';
      setBusy(false);
      controller = null;
    }
  }

  function summarize(message: Message) {
    // The on-demand LLM pass over the same question, as a fresh exchange.
    if (busy || !message.question) return;
    void ask(message.question, 'llm');
  }

  async function searchWeb(message: Message) {
    const query = message.notfound?.query;
    if (!query || message.webBusy) return;
    message.webBusy = true;
    try {
      const response = await fetch(`/api/search/web?q=${encodeURIComponent(query)}`);
      if (response.status === 401) {
        message.needsSignIn = true;
        return;
      }
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

<div class="thread">
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

<style>
  .thread {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    padding: 0.75rem 1rem 0.25rem;
  }
</style>
