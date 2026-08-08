<script lang="ts">
  // Bookmark toggle for the reading list, through the same-origin proxy.

  import { Bookmark, BookmarkCheck } from 'lucide-svelte';

  let { paperId, saved: initial }: { paperId: string; saved: boolean } = $props();

  let saved = $state(initial);
  let busy = $state(false);
  // The pop plays only when THIS interaction saved something. Keying it off .saved alone
  // would fire on every hydrated mount - a page full of saved bookmarks popping at once
  // on load and after every soft navigation.
  let justSaved = $state(false);
  // A failed toggle used to do nothing visible at all - the reader pressed save on a flaky
  // connection and concluded it worked. The bubble is role="status" so the outcome is
  // announced, and it clears itself so a stale warning never outlives its moment.
  let failed = $state(false);
  let failedTimer: ReturnType<typeof setTimeout> | undefined;

  async function toggle() {
    if (busy) return;
    busy = true;
    clearTimeout(failedTimer);
    failed = false;
    try {
      const response = await fetch(`/api/papers/${paperId}/save`, {
        method: saved ? 'DELETE' : 'POST',
      });
      if (response.ok) {
        saved = !saved;
        justSaved = saved;
      } else {
        failed = true;
      }
    } catch {
      failed = true;
    } finally {
      busy = false;
    }
    if (failed) {
      failedTimer = setTimeout(() => (failed = false), 4000);
    }
  }
</script>

<span class="save-wrap">
  <button
    class="save"
    class:saved
    class:pop={justSaved}
    onanimationend={() => (justSaved = false)}
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
  {#if failed}
    <span class="save-failed" role="status">Save failed - try again</span>
  {/if}
</span>

<style>
  .save-wrap {
    position: relative;
    display: inline-flex;
    flex-shrink: 0;
  }
  /* Floated under the button rather than in flow, so a failure never reflows the row it
     sits in. */
  .save-failed {
    position: absolute;
    top: calc(100% + 0.25rem);
    right: 0;
    padding: 0.15rem 0.55rem;
    border-radius: var(--radius-full, 999px);
    background: var(--danger-soft, #fee2e2);
    color: var(--danger-ink, #7f1d1d);
    font-size: 0.72rem;
    white-space: nowrap;
    pointer-events: none;
  }
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
      color var(--dur-fast, 0.15s) var(--ease-out, cubic-bezier(0.2, 0, 0, 1)),
      background-color var(--dur-fast, 0.15s) var(--ease-out, cubic-bezier(0.2, 0, 0, 1)),
      transform var(--dur-fast, 0.15s) var(--ease-out, cubic-bezier(0.2, 0, 0, 1));
  }
  .save:hover:not(:disabled) {
    background: var(--surface-2, #f4f0e8);
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
  /* A save just landed: the fresh check icon does one quick spring. The {#if} swap mounts
     a new svg each time, so repeats restart cleanly; animationend bubbles to the button
     and drops the class. */
  .save.pop :global(svg) {
    animation: save-pop var(--dur-slow, 0.25s) var(--ease-out, cubic-bezier(0.2, 0, 0, 1));
  }
  @keyframes save-pop {
    45% {
      transform: scale(1.18);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .save {
      transition: none;
    }
    .save.pop :global(svg) {
      animation: none;
    }
  }
</style>
