<script lang="ts">
  // The Scout half of the omnibox panel: rendering only. The conversation and the transport
  // behind it live in lib/chat-state.svelte.ts, where they survive this component - the
  // panel is mounted inside the omnibox's `{#if open}`, so closing it must not end a stream
  // or lose the thread, and reopening must show both. The pure pieces (frame splitting,
  // formatting, keyword matching) still live in src/lib; rendering is still ChatMessage's.

  import {
    chat,
    clearConversation,
    importHit,
    searchWeb,
    summarize,
  } from '../lib/chat-state.svelte';
  import ChatMessage from './ChatMessage.svelte';

  let { onactivity }: { onactivity: () => void } = $props();

  $effect(() => {
    // Track streamed text plus card and web-hit arrivals so the panel keeps the newest
    // content in view; the omnibox owns the scroll container, so it does the scrolling.
    void chat.messages.map((m) => m.text + (m.results?.length ?? 0) + (m.webHits?.length ?? 0));
    onactivity();
  });
</script>

<div class="thread">
  {#if chat.messages.length > 0}
    <p class="tools">
      <!-- The transcript now survives closing and reloading for a day, so forgetting it
           has to be a button rather than an accident. -->
      <button type="button" class="clear" onclick={clearConversation}>Clear conversation</button>
    </p>
  {/if}
  {#each chat.messages as message}
    <ChatMessage
      {message}
      busy={chat.busy}
      last={message === chat.messages[chat.messages.length - 1]}
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
    /* Exchanges breathe apart; the pieces inside one exchange stay close (ChatMessage
       owns its internal 0.4rem). One gutter, shared with the foot and the welcome. */
    gap: 0.9rem;
    padding: 0.9rem 1rem 0.4rem;
  }
  .tools {
    display: flex;
    justify-content: flex-end;
    margin: -0.4rem 0 -0.5rem;
  }
  .clear {
    border: none;
    background: none;
    padding: 0.2rem 0.3rem;
    color: var(--muted, #5d6570);
    font: inherit;
    font-size: var(--text-xs, 0.75rem);
    cursor: pointer;
    transition: color var(--dur-fast, 0.15s) var(--ease-out, ease);
  }
  .clear:hover {
    color: var(--ink, #17191c);
    text-decoration: underline;
  }
</style>
