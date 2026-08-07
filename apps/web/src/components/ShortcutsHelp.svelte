<script lang="ts">
  // The keyboard cheat sheet: a centered dialog on "?" (the settings drawer's button and
  // anything else carrying [data-open-shortcuts] open it too). DismissNotice's mechanics -
  // focus trap, counted scroll lock released on destroy, Escape, backdrop click.

  import { Keyboard, X } from 'lucide-svelte';
  import { onDestroy } from 'svelte';

  import { lockBodyScroll, trapFocus } from '../lib/overlay';

  let open = $state(false);
  let dialog: HTMLElement | undefined = $state();
  let previousFocus: Element | null = null;
  let unlockScroll: (() => void) | null = null;

  onDestroy(() => unlockScroll?.());

  const ROWS: { keys: string[]; does: string }[] = [
    { keys: ['Cmd K', 'Ctrl K'], does: 'Open the search and ask field' },
    { keys: ['/web', '/ai'], does: 'Prefix a question there to search the web or re-ask with AI' },
    { keys: ['Enter'], does: 'Open the highlighted result, or ask Scout' },
    { keys: ['Left', 'Right'], does: 'Turn pages in the PDF reader' },
    { keys: ['Esc'], does: 'Close whatever is open' },
    { keys: ['?'], does: 'This sheet' },
  ];

  function show() {
    if (open) return;
    previousFocus = document.activeElement;
    open = true;
    unlockScroll = lockBodyScroll();
  }

  function hide() {
    if (!open) return;
    open = false;
    unlockScroll?.();
    unlockScroll = null;
    if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
  }

  $effect(() => {
    if (open) dialog?.querySelector<HTMLElement>('button')?.focus();
  });

  function isEditable(target: EventTarget | null): boolean {
    return (
      target instanceof HTMLElement &&
      (target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.tagName === 'SELECT' ||
        target.isContentEditable)
    );
  }

  function onWindowKeydown(event: KeyboardEvent) {
    if (open && event.key === 'Escape') {
      hide();
      return;
    }
    // key already accounts for the layout: "?" is whatever produces a question mark here.
    // Shift is naturally down for it, so only the other modifiers disqualify.
    if (event.key === '?' && !event.metaKey && !event.ctrlKey && !event.altKey && !isEditable(event.target)) {
      event.preventDefault();
      show();
    }
  }

  function onDocumentClick(event: MouseEvent) {
    if ((event.target as Element).closest('[data-open-shortcuts]')) {
      event.preventDefault();
      show();
    }
  }

  function onDialogKeydown(event: KeyboardEvent) {
    if (dialog) trapFocus(dialog, event);
  }
</script>

<svelte:document onclick={onDocumentClick} />
<svelte:window onkeydown={onWindowKeydown} />

{#if open}
  <div
    class="backdrop"
    role="presentation"
    onclick={(event) => {
      if (event.target === event.currentTarget) hide();
    }}
  >
    <div
      class="shortcuts-card"
      role="dialog"
      aria-modal="true"
      aria-labelledby="shortcuts-title"
      bind:this={dialog}
      onkeydown={onDialogKeydown}
    >
      <header>
        <Keyboard size={17} aria-hidden="true" />
        <h2 id="shortcuts-title">Keyboard shortcuts</h2>
        <button class="close" onclick={hide} aria-label="Close the shortcuts sheet">
          <X size={18} aria-hidden="true" />
        </button>
      </header>
      <dl>
        {#each ROWS as row}
          <div class="row">
            <dt>
              {#each row.keys as key, index}
                {#if index > 0}<span class="or">or</span>{/if}
                <kbd>{key}</kbd>
              {/each}
            </dt>
            <dd>{row.does}</dd>
          </div>
        {/each}
      </dl>
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    /* The notices' level: the last thing opened, above the drawers. */
    z-index: 60;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--gutter, 1.5rem);
    background: color-mix(in srgb, var(--bg, #faf7f1) 72%, transparent);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    animation: shortcuts-fade var(--dur-fast, 0.15s) var(--ease-out, ease);
  }
  .shortcuts-card {
    width: min(26rem, 100%);
    max-height: min(32rem, 85dvh);
    overflow-y: auto;
    padding: var(--space-5, 1.25rem);
    border-radius: var(--radius-md, 14px);
    background: var(--surface, #fff);
    box-shadow: var(--shadow-lg, 0 16px 48px rgb(23 25 28 / 0.16));
    animation: shortcuts-in var(--dur-slow, 0.25s) var(--ease-out, ease);
  }
  @keyframes shortcuts-fade {
    from {
      opacity: 0;
    }
  }
  @keyframes shortcuts-in {
    from {
      opacity: 0;
      transform: translateY(10px) scale(0.98);
    }
  }
  header {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    margin-bottom: 0.9rem;
    color: var(--ink, #17191c);
  }
  h2 {
    margin: 0;
    font-size: 1.05rem;
    font-weight: 650;
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
  }
  .close:hover {
    background: var(--surface-2, #f4f0e8);
    color: var(--ink, #17191c);
  }
  dl {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 0.55rem;
  }
  .row {
    display: flex;
    align-items: baseline;
    gap: 0.8rem;
  }
  dt {
    flex-shrink: 0;
    min-width: 8.5rem;
    display: inline-flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 0.3rem;
  }
  kbd {
    padding: 0.15rem 0.45rem;
    border: 1px solid var(--line, #e6e1d5);
    border-bottom-width: 2px;
    border-radius: 6px;
    background: var(--surface-2, #f4f0e8);
    color: var(--ink, #17191c);
    font-family: var(--font-sans, sans-serif);
    font-size: 0.78rem;
    white-space: nowrap;
  }
  .or {
    color: var(--muted, #5d6570);
    font-size: 0.75rem;
  }
  dd {
    margin: 0;
    color: var(--muted, #5d6570);
    font-size: var(--text-sm, 0.875rem);
    line-height: 1.5;
  }
</style>
