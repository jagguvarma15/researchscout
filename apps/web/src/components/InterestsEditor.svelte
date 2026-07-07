<script lang="ts">
  // Research-interests editor: chips with remove buttons plus an add field. Every
  // change is saved wholesale through the authenticated proxy (PUT /api/me/interests).

  import { Plus, X } from 'lucide-svelte';

  const MAX_INTERESTS = 20;
  const MAX_LENGTH = 40;

  let interests = $state<string[]>([]);
  let draft = $state('');
  let busy = $state(false);
  let loaded = $state(false);
  let error = $state('');

  $effect(() => {
    void load();
  });

  async function load() {
    try {
      const response = await fetch('/api/me/interests');
      if (response.ok) {
        interests = ((await response.json()) as { interests: string[] }).interests;
      } else {
        error = 'Could not load your interests — refresh to try again.';
      }
    } catch {
      error = 'Could not load your interests — refresh to try again.';
    } finally {
      loaded = true;
    }
  }

  async function save(next: string[]) {
    busy = true;
    error = '';
    try {
      const response = await fetch('/api/me/interests', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ interests: next }),
      });
      if (response.ok) {
        interests = ((await response.json()) as { interests: string[] }).interests;
      } else {
        error = 'Saving failed — try again.';
      }
    } catch {
      error = 'Saving failed — try again.';
    } finally {
      busy = false;
    }
  }

  function add(event: SubmitEvent) {
    event.preventDefault();
    const interest = draft.trim();
    if (!interest || busy) return;
    if (interest.length > MAX_LENGTH) {
      error = `Keep interests under ${MAX_LENGTH} characters.`;
      return;
    }
    if (interests.includes(interest)) {
      error = 'Already on the list.';
      return;
    }
    if (interests.length >= MAX_INTERESTS) {
      error = `That's the cap — remove one first (max ${MAX_INTERESTS}).`;
      return;
    }
    draft = '';
    void save([...interests, interest]);
  }

  function remove(interest: string) {
    if (busy) return;
    void save(interests.filter((item) => item !== interest));
  }
</script>

<div class="editor">
  {#if !loaded}
    <p class="hint">Loading your interests…</p>
  {:else if interests.length === 0}
    <p class="hint">No interests yet — add a topic to tune the radar.</p>
  {:else}
    <ul class="chips">
      {#each interests as interest}
        <li class="chip">
          <span>{interest}</span>
          <button
            class="remove"
            onclick={() => remove(interest)}
            disabled={busy}
            aria-label={`Remove ${interest}`}
            title={`Remove ${interest}`}
          >
            <X size={13} aria-hidden="true" />
          </button>
        </li>
      {/each}
    </ul>
  {/if}

  <form onsubmit={add}>
    <input
      type="text"
      placeholder="e.g. reinforcement learning"
      maxlength={MAX_LENGTH}
      bind:value={draft}
      disabled={busy}
      aria-label="New interest"
    />
    <button type="submit" disabled={busy || !draft.trim()}>
      <Plus size={16} aria-hidden="true" />
      Add
    </button>
  </form>

  {#if error}
    <p class="error" role="alert">{error}</p>
  {/if}
</div>

<style>
  .hint {
    margin: 0;
    color: var(--muted, #5d6570);
    font-size: 0.9rem;
  }
  .chips {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 0.15rem;
    padding: 0.2rem 0.3rem 0.2rem 0.75rem;
    border-radius: 999px;
    background: var(--surface-2, #f5f7fa);
    color: var(--ink, #17191c);
    font-size: 0.85rem;
  }
  .remove {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.4rem;
    height: 1.4rem;
    border: none;
    border-radius: 999px;
    background: none;
    color: var(--muted, #5d6570);
    cursor: pointer;
    transition: background-color 0.15s ease;
  }
  .remove:hover:not(:disabled) {
    background: #e6eaef;
    color: var(--ink, #17191c);
  }
  .remove:focus-visible {
    outline: 2px solid var(--accent, #0f62fe);
    outline-offset: 1px;
  }
  .remove:disabled {
    opacity: 0.5;
    cursor: default;
  }
  form {
    display: flex;
    gap: 0.5rem;
    margin-top: 1rem;
    max-width: 26rem;
  }
  input {
    flex: 1;
    min-width: 0;
    padding: 0.5rem 0.8rem;
    border: 1px solid var(--line, #e4e7eb);
    border-radius: 10px;
    background: #fff;
    font: inherit;
    font-size: 0.9rem;
  }
  input::placeholder {
    color: var(--muted, #5d6570);
  }
  input:focus-visible {
    outline: 2px solid var(--accent, #0f62fe);
    outline-offset: 1px;
    border-color: var(--accent, #0f62fe);
  }
  form button {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.5rem 1rem;
    border: none;
    border-radius: 999px;
    background: var(--accent, #0f62fe);
    color: #fff;
    font: inherit;
    font-size: 0.88rem;
    font-weight: 550;
    cursor: pointer;
    transition: background-color 0.15s ease;
  }
  form button:hover:not(:disabled) {
    background: var(--accent-hover, #0043ce);
  }
  form button:focus-visible {
    outline: 2px solid var(--accent, #0f62fe);
    outline-offset: 2px;
  }
  form button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .error {
    margin: 0.6rem 0 0;
    color: #8b1d1d;
    font-size: 0.85rem;
  }
  @media (prefers-reduced-motion: reduce) {
    .remove,
    form button {
      transition: none;
    }
  }
</style>
