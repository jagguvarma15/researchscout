<script lang="ts">
  // The dialog shown when a paper is dismissed, saying what happened to it.
  //
  // Dismissing takes the row out of the feed and keeps it out on the next visit, which is a
  // large enough thing to do quietly that it is said out loud - and, since it is not reversible
  // by repeating it, said with a way back. Undo is the reason this dialog earns its interruption
  // rather than being a toast that has already faded by the time the mistake is noticed.
  //
  // Telemetry.svelte does the removing and dispatches `rs:dismissed` with the function that puts
  // the row back; this only listens. Keeping them apart means the notice can be absent - on a
  // page with no feed - without dismissal breaking.

  import { Check } from 'lucide-svelte';

  import { lockBodyScroll, trapFocus } from '../lib/overlay';

  let open = $state(false);
  let title = $state('');
  let dialog: HTMLElement | undefined = $state();
  let previousFocus: Element | null = null;
  let unlockScroll: (() => void) | null = null;
  let undo: (() => void) | null = null;

  function show(event: Event) {
    const detail = (event as CustomEvent<{ title?: string; undo?: () => void }>).detail;
    title = detail?.title ?? '';
    undo = detail?.undo ?? null;
    previousFocus = document.activeElement;
    open = true;
    unlockScroll = lockBodyScroll();
  }

  function hide() {
    if (!open) return;
    open = false;
    undo = null;
    unlockScroll?.();
    unlockScroll = null;
    // The Dismiss button went with the row, so returning focus to it is not an option. The
    // feed heading is where the reader was looking, and it is a stable target.
    const fallback = document.querySelector<HTMLElement>('.feed, main, body');
    const target = previousFocus instanceof HTMLElement && previousFocus.isConnected
      ? previousFocus
      : fallback;
    target?.focus?.();
  }

  function undoAndHide() {
    undo?.();
    hide();
  }

  $effect(() => {
    // Okay, not the first button in the markup: Undo comes first visually, and focusing it
    // would make Enter - the reflex for closing a dialog - silently reverse the dismissal.
    if (open) dialog?.querySelector<HTMLElement>('.btn-primary')?.focus();
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
      <h2 id="dismiss-notice-title">Taken out of your feed</h2>
      {#if title}
        <p class="what">{title}</p>
      {/if}
      <p class="how">
        It will not come back on a reload. Search still finds it, and its own page still opens.
      </p>
      <div class="actions">
        {#if undo}
          <button class="btn btn-ghost" type="button" onclick={undoAndHide}>Undo</button>
        {/if}
        <button class="btn btn-primary" type="button" onclick={hide}>Okay</button>
      </div>
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
  .actions {
    display: flex;
    gap: 0.5rem;
  }
  /* Both full width and equal: Okay is the expected answer, Undo the one that has to be
     findable in a hurry, and neither should be the small target. */
  .actions button {
    flex: 1;
  }
</style>
