<script lang="ts">
  // The Scout half of the omnibox panel: rendering only. The conversation and the transport
  // behind it live in lib/chat-state.svelte.ts, where they survive this component - the
  // panel is mounted inside the omnibox's `{#if open}`, so closing it must not end a stream
  // or lose the thread, and reopening must show both. The pure pieces (frame splitting,
  // formatting, keyword matching) still live in src/lib; rendering is still ChatMessage's.

  import { chat, importHit, searchWeb, summarize } from '../lib/chat-state.svelte';
  import ChatMessage from './ChatMessage.svelte';

  let { onactivity }: { onactivity: () => void } = $props();

  $effect(() => {
    // Track streamed text plus card and web-hit arrivals so the panel keeps the newest
    // content in view; the omnibox owns the scroll container, so it does the scrolling.
    // Only the last message and the thread length: this effect fires per streamed token,
    // and touching every message's full text re-concatenated the whole transcript each
    // time - O(history) garbage per token. Streams and web results only ever mutate the
    // tail message, so watching it is enough.
    const last = chat.messages[chat.messages.length - 1];
    void chat.messages.length;
    void (last ? last.text.length + (last.results?.length ?? 0) + (last.webHits?.length ?? 0) : 0);
    onactivity();
  });
</script>

<div class="thread">
  <!-- Clear conversation lives in the omnibox foot row: the transcript should start with
       the conversation, not a control. -->
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
</style>
