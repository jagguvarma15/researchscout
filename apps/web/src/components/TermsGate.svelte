<script lang="ts">
  // The one blocking dialog in the app: a new account cannot use the site until it accepts the
  // current terms. Rendered by the server only when the account actually owes an acceptance,
  // so there is no flash of a modal for people who already accepted.
  //
  // Deliberately not dismissable - no Escape, no backdrop click. The way out is to accept or
  // to sign out, and both are on screen.
  import { lockBodyScroll, trapFocus } from '../lib/overlay';
  import { TERMS_SUMMARY } from '../lib/legal';

  let { version }: { version: string } = $props();

  let dialog = $state<HTMLElement | null>(null);
  let acceptButton = $state<HTMLButtonElement | null>(null);
  let busy = $state(false);
  let error = $state('');
  let done = $state(false);

  $effect(() => {
    if (done) return;
    const unlock = lockBodyScroll();
    acceptButton?.focus();
    return unlock;
  });

  async function accept(): Promise<void> {
    busy = true;
    error = '';
    try {
      const response = await fetch('/api/me/terms', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ version }),
      });
      if (!response.ok) {
        // A 409 means the terms moved under us; a reload picks up the new version.
        error =
          response.status === 409
            ? 'These terms have been updated. Reload the page to see the current version.'
            : 'That did not go through. Try again in a moment.';
        return;
      }
      done = true;
    } catch {
      error = 'That did not go through. Check your connection and try again.';
    } finally {
      busy = false;
    }
  }
</script>

{#if !done}
  <div class="backdrop backdrop-veil" role="presentation">
    <div
      class="gate"
      role="dialog"
      aria-modal="true"
      aria-labelledby="terms-gate-title"
      bind:this={dialog}
      onkeydown={(event) => dialog && trapFocus(dialog, event)}
    >
      <h2 id="terms-gate-title">Before you start</h2>
      <p class="lede">
        A quick summary of the terms you are agreeing to. The full text is short and worth
        reading.
      </p>
      <ul>
        {#each TERMS_SUMMARY as line (line)}
          <li>{line}</li>
        {/each}
      </ul>
      <p class="links">
        <a href="/terms" target="_blank" rel="noopener">Terms of Use</a>
        <a href="/privacy" target="_blank" rel="noopener">Privacy notice</a>
      </p>
      {#if error}
        <p class="error" role="alert">{error}</p>
      {/if}
      <div class="actions">
        <a class="btn btn-ghost" href="/logout">Not now, sign out</a>
        <button
          class="btn btn-primary"
          type="button"
          onclick={accept}
          disabled={busy}
          bind:this={acceptButton}
        >
          {busy ? 'Saving...' : 'Accept and continue'}
        </button>
      </div>
      <p class="version">Version {version}</p>
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: var(--z-notice, 60);
    display: grid;
    place-items: center;
    padding: 1rem;
  }
  .gate {
    width: min(34rem, 100%);
    max-height: 90dvh;
    overflow-y: auto;
    padding: 1.5rem 1.6rem;
    border-radius: var(--radius-md);
    background: var(--surface);
    border: 1px solid var(--line);
  }
  h2 {
    margin: 0 0 0.4rem;
    font-size: var(--text-lg);
    letter-spacing: -0.01em;
  }
  .lede {
    margin: 0 0 0.9rem;
    color: var(--muted);
    font-size: var(--text-sm);
    line-height: 1.6;
  }
  ul {
    margin: 0 0 1rem;
    padding-left: 1.2rem;
    font-size: var(--text-sm);
    line-height: 1.6;
  }
  li {
    margin-bottom: 0.5rem;
  }
  .links {
    display: flex;
    gap: 1rem;
    margin: 0 0 1rem;
    font-size: var(--text-sm);
  }
  .error {
    margin: 0 0 0.8rem;
    color: var(--accent);
    font-size: var(--text-sm);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    justify-content: flex-end;
    align-items: center;
  }
  .version {
    margin: 0.9rem 0 0;
    color: var(--muted);
    font-size: var(--text-xs);
    text-align: right;
  }
</style>
