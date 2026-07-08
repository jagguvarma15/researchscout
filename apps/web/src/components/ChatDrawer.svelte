<script lang="ts">
  // The chat side panel: a single island, closed by default. Talks to the API through the
  // authenticated same-origin proxy (/api/chat) and renders the SSE stream token by token.

  import { Lock, MessageCircle, Send, X } from 'lucide-svelte';

  interface UsedPaper {
    id: string;
    title: string;
    score: number;
  }

  interface Message {
    role: 'user' | 'assistant';
    text: string;
    cited?: string[];
    used?: UsedPaper[];
    error?: boolean;
  }

  let { authenticated }: { authenticated: boolean } = $props();

  let open = $state(false);
  let input = $state('');
  let busy = $state(false);
  let messages = $state<Message[]>([]);
  let scroller: HTMLElement | undefined = $state();

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
    if (event === 'token') {
      current.text += payload.delta;
    } else if (event === 'done') {
      current.cited = payload.cited;
      current.used = payload.used;
    } else if (event === 'error') {
      current.text = payload.message ?? 'Something went wrong.';
      current.error = true;
    }
  }

  async function send(event: SubmitEvent) {
    event.preventDefault();
    const question = input.trim();
    if (!question || busy) return;
    input = '';
    busy = true;
    messages.push({ role: 'user', text: question });
    const current: Message = { role: 'assistant', text: '' };
    messages.push(current);

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ question }),
      });
      if (response.status === 401) {
        current.text = 'Your session expired — sign in again to keep chatting.';
        current.error = true;
        return;
      }
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
      busy = false;
    }
  }
</script>

<button
  class="fab"
  onclick={() => (open = !open)}
  aria-expanded={open}
  aria-label={open ? 'Close the chat panel' : 'Ask about papers'}
>
  {#if open}
    <X size={22} aria-hidden="true" />
  {:else}
    <MessageCircle size={22} aria-hidden="true" />
  {/if}
</button>

<aside class="drawer" class:open aria-label="Ask about research papers" aria-hidden={!open}>
  <header>
    <strong>Ask about papers</strong>
    <button class="close" onclick={() => (open = false)} aria-label="Close">
      <X size={18} aria-hidden="true" />
    </button>
  </header>

  {#if !authenticated}
    <div class="gate">
      <span class="gate-mark"><Lock size={20} aria-hidden="true" /></span>
      <p>Chat is for signed-in readers.</p>
      <a class="signin" href="/auth/login">Sign in to ask</a>
    </div>
  {:else}
    <div class="messages" bind:this={scroller}>
      {#if messages.length === 0}
        <p class="hint">
          Ask anything about the papers on the radar — answers cite what they rely on.
        </p>
      {/if}
      {#each messages as message}
        <div class="msg {message.role}" class:error={message.error}>
          <p>{message.text}{#if message.role === 'assistant' && busy && message === messages[messages.length - 1]}<span class="cursor">▍</span>{/if}</p>
          {#if message.cited && message.cited.length > 0}
            <p class="citations">
              {#each message.used ?? [] as paper}
                {#if message.cited.includes(paper.id)}
                  <a href={`/papers/${paper.id}`} title={paper.title}>{paper.id}</a>
                {/if}
              {/each}
            </p>
          {/if}
        </div>
      {/each}
    </div>
    <form onsubmit={send}>
      <input
        type="text"
        placeholder="What's new in reinforcement learning?"
        bind:value={input}
        disabled={busy}
      />
      <button type="submit" disabled={busy || !input.trim()} aria-label="Send">
        <Send size={17} aria-hidden="true" />
      </button>
    </form>
  {/if}
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
  .gate {
    padding: 2.5rem 1.25rem;
    text-align: center;
  }
  .gate-mark {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2.75rem;
    height: 2.75rem;
    border-radius: 999px;
    background: var(--accent-soft, #fef3c7);
    color: var(--accent-ink, #78350f);
  }
  .gate p {
    margin: 0.75rem 0 0;
    color: var(--muted, #5d6570);
  }
  .signin {
    display: inline-block;
    margin-top: 1rem;
    padding: 0.5rem 1.25rem;
    border-radius: 999px;
    background: var(--accent, #c2410c);
    color: var(--accent-contrast, #fff);
    font-weight: 550;
    font-size: 0.9rem;
    text-decoration: none;
    box-shadow: 0 1px 2px rgb(23 25 28 / 0.1);
    transition: background-color 0.15s ease;
  }
  .signin:hover {
    background: var(--accent-hover, #9a3412);
  }
  .signin:focus-visible {
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
  @media (prefers-reduced-motion: reduce) {
    .cursor {
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
