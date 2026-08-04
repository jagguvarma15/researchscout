<script lang="ts">
  // The dialog shown when a paper is dismissed, saying where it went.
  //
  // Dismiss used to remove the card outright, which left nothing to explain. It now moves the
  // paper to the end of its day and remembers that for next time, and a row that quietly slides
  // somewhere else is exactly the kind of change a reader will assume was a bug. So it is said
  // out loud, once per dismissal, with one button.
  //
  // Telemetry.svelte does the moving and dispatches `rs:dismissed` on the document; this only
  // listens. Keeping them apart means the notice can be absent - on a page with no feed - without
  // dismissal breaking.

  import { Check } from 'lucide-svelte';

  import { lockBodyScroll, trapFocus } from '../lib/overlay';

  let open = $state(false);
  let title = $state('');
  let dialog: HTMLElement | undefined = $state();
  let previousFocus: Element | null = null;
  let unlockScroll: (() => void) | null = null;

  function show(event: Event) {
    const detail = (event as CustomEvent<{ title?: string }>).detail;
    title = detail?.title ?? '';
    previousFocus = document.activeElement;
    open = true;
    unlockScroll = lockBodyScroll();
  }

  function hide() {
    if (!open) return;
    open = false;
    unlockScroll?.();
    unlockScroll = null;
    // Back to the Dismiss button that opened this, which is where the reader was.
    if (previousFocus instanceof HTMLElement) previousFocus.focus();
  }

  $effect(() => {
    if (open) dialog?.querySelector<HTMLElement>('button')?.focus();
  });

  function onKeydown(event: KeyboardEvent) {
    if (!open) return;
    // Escape as well as the button: a dialog with one acknowledgement should never be a trap.
    if (event.key === 'Escape') hide();
    else if (dialog) trapFocus(dialog, event);
  }
</script>

<svelte:document on:rs:dismissed={show} />
<svelte:window onkeydown={onKeydown} />

{#if open}
  <div
    class="backdrop"
    role="presentation"
    onclick={(event) => {
      if (event.target === event.currentTarget) hide();
    }}
  >
    <div
      class="notice"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="dismiss-notice-title"
      bind:this={dialog}
    >
      <span class="mark" aria-hidden="true"><Check size={18} /></span>
      <h2 id="dismiss-notice-title">Moved to the end of the list</h2>
      {#if title}
        <p class="what">{title}</p>
      {/if}
      <p class="how">It stays in the feed and stays searchable. Dismiss it again to bring it back.</p>
      <button class="btn btn-primary" type="button" onclick={hide}>Okay</button>
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    /* Above the filter sidebar (35) and the rail (38): this is the last thing opened. */
    z-index: 60;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--gutter);
    background: rgb(0 0 0 / 0.32);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
  }
  .notice {
    width: min(24rem, 100%);
    padding: var(--space-5) var(--space-5) var(--space-4);
    border-radius: var(--radius-md);
    background: var(--surface);
    box-shadow: var(--shadow-lg);
    text-align: center;
  }
  .mark {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2.25rem;
    height: 2.25rem;
    margin-bottom: 0.6rem;
    border-radius: var(--radius-full);
    background: var(--accent-soft);
    color: var(--accent-ink);
  }
  .notice h2 {
    margin: 0;
    font-size: 1.05rem;
    font-weight: 650;
  }
  .what {
    margin: 0.5rem 0 0;
    color: var(--ink);
    font-size: var(--text-sm);
    /* One line: this is a reminder of which paper, not the paper. */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .how {
    margin: 0.5rem 0 var(--space-4);
    color: var(--muted);
    font-size: var(--text-sm);
    line-height: 1.5;
  }
  .notice button {
    width: 100%;
  }
</style>
