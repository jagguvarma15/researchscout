<script lang="ts">
  // The chat side panel: a single island, closed by default. Talks to the API through the
  // authenticated same-origin proxy (/api/chat) and renders the SSE stream token by token.

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

<button class="fab" onclick={() => (open = !open)} aria-expanded={open}>
  {open ? 'Close' : 'Ask'}
</button>

<aside class="drawer" class:open aria-label="Ask about research papers" aria-hidden={!open}>
  <header>
    <strong>Ask about papers</strong>
    <button class="close" onclick={() => (open = false)}>&times;</button>
  </header>

  {#if !authenticated}
    <div class="gate">
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
      <button type="submit" disabled={busy || !input.trim()}>Send</button>
    </form>
  {/if}
</aside>

<style>
  .fab {
    position: fixed;
    right: 1.25rem;
    bottom: 1.25rem;
    z-index: 30;
    padding: 0.6rem 1.1rem;
    border: none;
    border-radius: 999px;
    background: var(--accent, #0f62fe);
    color: #fff;
    font: inherit;
    font-weight: 600;
    cursor: pointer;
    box-shadow: 0 4px 14px rgb(0 0 0 / 0.18);
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
    background: #fff;
    border-left: 1px solid var(--line, #e3e6e8);
    box-shadow: -8px 0 24px rgb(0 0 0 / 0.06);
    transform: translateX(100%);
    transition: transform 0.2s ease;
  }
  .drawer.open {
    transform: translateX(0);
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 1rem 1.25rem;
    border-bottom: 1px solid var(--line, #e3e6e8);
  }
  .close {
    border: none;
    background: none;
    font-size: 1.4rem;
    line-height: 1;
    cursor: pointer;
    color: var(--muted, #6a7076);
  }
  .gate {
    padding: 2rem 1.25rem;
    text-align: center;
  }
  .signin {
    display: inline-block;
    margin-top: 0.5rem;
    padding: 0.5rem 1rem;
    border-radius: 8px;
    background: var(--accent, #0f62fe);
    color: #fff;
    text-decoration: none;
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
    color: var(--muted, #6a7076);
    font-size: 0.9rem;
  }
  .msg p {
    margin: 0;
    padding: 0.55rem 0.8rem;
    border-radius: 10px;
    font-size: 0.92rem;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .msg.user p {
    background: var(--accent, #0f62fe);
    color: #fff;
    margin-left: 2rem;
  }
  .msg.assistant p {
    background: #f2f4f6;
    margin-right: 2rem;
  }
  .msg.error p {
    background: #fdecec;
    color: #8b1d1d;
  }
  .citations {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    padding: 0.35rem 0 0 !important;
    background: none !important;
  }
  .citations a {
    font-size: 0.78rem;
    border: 1px solid var(--line, #e3e6e8);
    border-radius: 999px;
    padding: 0.05rem 0.5rem;
    text-decoration: none;
    color: var(--accent, #0f62fe);
  }
  .cursor {
    animation: blink 1s step-start infinite;
  }
  @keyframes blink {
    50% {
      opacity: 0;
    }
  }
  form {
    display: flex;
    gap: 0.5rem;
    padding: 0.9rem 1.25rem;
    border-top: 1px solid var(--line, #e3e6e8);
  }
  input {
    flex: 1;
    padding: 0.5rem 0.7rem;
    border: 1px solid var(--line, #e3e6e8);
    border-radius: 8px;
    font: inherit;
  }
  form button {
    padding: 0.5rem 0.9rem;
    border: none;
    border-radius: 8px;
    background: var(--accent, #0f62fe);
    color: #fff;
    font: inherit;
    cursor: pointer;
  }
  form button:disabled {
    opacity: 0.5;
    cursor: default;
  }
</style>
