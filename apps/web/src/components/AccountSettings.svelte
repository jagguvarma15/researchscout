<script lang="ts">
  // The account panel on the profile page: rename yourself, take your data, or leave.
  //
  // Deletion asks you to type the word rather than clicking a second button, because it takes
  // the login at the identity provider with it and nothing here can undo that.

  let {
    displayName = '',
    termsVersion = null,
    canDelete = false,
  }: { displayName?: string; termsVersion?: string | null; canDelete?: boolean } = $props();

  let name = $state(displayName);
  let saving = $state(false);
  let saved = $state(false);
  let message = $state('');
  let confirmation = $state('');
  let deleting = $state(false);

  const CONFIRM_WORD = 'delete';

  async function saveName(): Promise<void> {
    saving = true;
    saved = false;
    message = '';
    try {
      const response = await fetch('/api/me', {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ display_name: name.trim() }),
      });
      if (!response.ok) {
        message = 'Could not save that name.';
        return;
      }
      saved = true;
    } catch {
      message = 'Could not save that name.';
    } finally {
      saving = false;
    }
  }

  function downloadData(): void {
    // A plain navigation: the proxy streams the JSON and the browser saves it.
    window.location.href = '/api/me/export';
  }

  async function deleteAccount(): Promise<void> {
    deleting = true;
    message = '';
    try {
      const response = await fetch('/api/me', { method: 'DELETE' });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({ detail: '' }));
        message =
          typeof detail?.detail === 'string' && detail.detail
            ? `Nothing was deleted: ${detail.detail}`
            : 'Nothing was deleted. Try again later.';
        return;
      }
      window.location.href = '/logout';
    } catch {
      message = 'Nothing was deleted. Check your connection and try again.';
    } finally {
      deleting = false;
    }
  }
</script>

<div class="account">
  <label class="field">
    <span>Display name</span>
    <input
      class="input"
      type="text"
      bind:value={name}
      maxlength="80"
      placeholder="How the site should address you"
    />
  </label>
  <div class="row">
    <button class="btn btn-primary" type="button" onclick={saveName} disabled={saving || !name.trim()}>
      {saving ? 'Saving...' : 'Save name'}
    </button>
    {#if saved}<span class="ok">Saved</span>{/if}
  </div>

  <div class="row spaced">
    <div>
      <h3>Your data</h3>
      <p>Everything stored about you, as JSON: account, saved papers, interests, reading events.</p>
    </div>
    <button class="btn btn-ghost" type="button" onclick={downloadData}>Download</button>
  </div>

  {#if termsVersion}
    <p class="terms">You accepted the terms, version {termsVersion}.</p>
  {/if}

  {#if canDelete}
    <div class="danger">
      <h3>Delete this account</h3>
      <p>
        This removes your login and every row tied to it - saved papers, interests and reading
        history - and cannot be undone. Download your data first if you want a copy.
      </p>
      <label class="field">
        <span>Type <code>{CONFIRM_WORD}</code> to confirm</span>
        <input class="input" type="text" bind:value={confirmation} autocomplete="off" />
      </label>
      <button
        class="btn danger-btn"
        type="button"
        onclick={deleteAccount}
        disabled={deleting || confirmation.trim().toLowerCase() !== CONFIRM_WORD}
      >
        {deleting ? 'Deleting...' : 'Delete my account'}
      </button>
    </div>
  {/if}

  {#if message}<p class="message" role="alert">{message}</p>{/if}
</div>

<style>
  .account {
    max-width: 46rem;
  }
  .field {
    display: block;
    margin-bottom: 0.7rem;
  }
  .field span {
    display: block;
    margin-bottom: 0.3rem;
    font-size: var(--text-sm);
  }
  .field .input {
    width: min(24rem, 100%);
  }
  .row {
    display: flex;
    align-items: center;
    gap: 0.7rem;
  }
  .row.spaced {
    justify-content: space-between;
    align-items: flex-start;
    gap: 1.5rem;
    margin-top: var(--space-5);
    padding-top: var(--space-5);
    border-top: 1px solid var(--line);
  }
  h3 {
    margin: 0 0 0.25rem;
    font-size: var(--text-md);
  }
  p {
    max-width: 60ch;
    margin: 0;
    color: var(--muted);
    font-size: var(--text-sm);
    line-height: 1.6;
  }
  .ok {
    color: var(--muted);
    font-size: var(--text-sm);
  }
  .terms {
    margin-top: var(--space-4);
    font-size: var(--text-xs);
  }
  .danger {
    margin-top: var(--space-5);
    padding-top: var(--space-5);
    border-top: 1px solid var(--line);
  }
  .danger p {
    margin-bottom: 0.8rem;
  }
  .danger-btn {
    border: 1px solid var(--line);
    background: var(--surface);
    color: var(--accent);
  }
  .danger-btn:hover:not(:disabled) {
    background: var(--accent-soft);
  }
  .message {
    margin-top: 0.8rem;
    color: var(--accent);
  }
</style>
