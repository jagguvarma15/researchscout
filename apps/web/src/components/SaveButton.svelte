<script lang="ts">
  // Bookmark toggle for the reading list, through the same-origin proxy.

  import { Bookmark, BookmarkCheck } from 'lucide-svelte';

  let { paperId, saved: initial }: { paperId: string; saved: boolean } = $props();

  let saved = $state(initial);
  let busy = $state(false);

  async function toggle() {
    if (busy) return;
    busy = true;
    try {
      const response = await fetch(`/api/papers/${paperId}/save`, {
        method: saved ? 'DELETE' : 'POST',
      });
      if (response.ok) saved = !saved;
    } finally {
      busy = false;
    }
  }
</script>

<button
  class="save"
  class:saved
  onclick={toggle}
  disabled={busy}
  aria-pressed={saved}
  aria-label={saved ? 'Remove from reading list' : 'Save to reading list'}
  title={saved ? 'Remove from reading list' : 'Save to reading list'}
>
  {#if saved}
    <BookmarkCheck size={18} fill="currentColor" fill-opacity={0.18} aria-hidden="true" />
  {:else}
    <Bookmark size={18} aria-hidden="true" />
  {/if}
</button>

<style>
  .save {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 2rem;
    flex-shrink: 0;
    border: 1px solid transparent;
    border-radius: 999px;
    background: none;
    cursor: pointer;
    color: var(--muted, #5d6570);
    transition:
      color 0.15s ease,
      background-color 0.15s ease,
      transform 0.15s ease;
  }
  .save:hover:not(:disabled) {
    background: var(--surface-2, #f5f7fa);
    color: var(--ink, #17191c);
  }
  .save:active:not(:disabled) {
    transform: scale(0.92);
  }
  .save:focus-visible {
    outline: 2px solid var(--accent, #c2410c);
    outline-offset: 2px;
  }
  .save:disabled {
    opacity: 0.6;
    cursor: default;
  }
  .save.saved {
    color: var(--accent, #c2410c);
  }
  .save.saved:hover:not(:disabled) {
    background: var(--accent-soft, #fef3c7);
    color: var(--accent-hover, #9a3412);
  }
  @media (prefers-reduced-motion: reduce) {
    .save {
      transition: none;
    }
  }
</style>
