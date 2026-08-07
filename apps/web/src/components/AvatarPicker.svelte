<script lang="ts">
  // The crew picker on the profile page: every drawn avatar plus the initials fallback,
  // saved the moment one is chosen - a preference this small should not wait for a save
  // button. A failed save puts the previous choice back rather than lying about it.

  import { AVATARS } from '../lib/avatars';
  import AvatarArt from './AvatarArt.svelte';

  let { current = null, initials = '?' }: { current?: string | null; initials?: string } =
    $props();

  let chosen = $state(current);
  let saving = $state(false);
  let saved = $state(false);
  let message = $state('');

  async function choose(slug: string | null): Promise<void> {
    const previous = chosen;
    chosen = slug;
    saving = true;
    saved = false;
    message = '';
    try {
      // Empty string is the API's "clear it" spelling; absent would mean "leave it".
      const response = await fetch('/api/me', {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ avatar: slug ?? '' }),
      });
      if (!response.ok) {
        chosen = previous;
        message = 'Could not save that choice.';
        return;
      }
      saved = true;
    } catch {
      chosen = previous;
      message = 'Could not save that choice.';
    } finally {
      saving = false;
    }
  }
</script>

<div class="avatar-grid" role="group" aria-label="Choose an avatar">
  {#each AVATARS as choice}
    <button
      type="button"
      class="pick"
      aria-pressed={chosen === choice.slug}
      onclick={() => choose(choice.slug)}
      disabled={saving}
    >
      <AvatarArt slug={choice.slug} size={56} />
      <span class="name">{choice.label}</span>
    </button>
  {/each}
  <button
    type="button"
    class="pick"
    aria-pressed={chosen === null}
    onclick={() => choose(null)}
    disabled={saving}
  >
    <span class="initials" aria-hidden="true">{initials}</span>
    <span class="name">Initials</span>
  </button>
</div>
<p class="status" role="status">
  {#if saved}Saved{:else if message}{message}{/if}
</p>

<style>
  .avatar-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(5.5rem, 1fr));
    gap: 0.6rem;
  }
  .pick {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.4rem;
    padding: 0.7rem 0.4rem 0.55rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-sm, 10px);
    background: var(--surface, #fff);
    color: var(--ink, #17191c);
    cursor: pointer;
    transition:
      border-color var(--dur-fast, 0.15s) var(--ease-out, ease),
      background-color var(--dur-fast, 0.15s) var(--ease-out, ease);
  }
  .pick:hover:not(:disabled) {
    border-color: var(--line-strong, #d1d6dc);
  }
  .pick[aria-pressed='true'] {
    border-color: var(--accent, #c2410c);
    background: var(--accent-soft, #fef3c7);
  }
  .pick:disabled {
    cursor: default;
  }
  .name {
    color: var(--muted, #5d6570);
    font-size: var(--text-xs, 0.75rem);
  }
  .pick[aria-pressed='true'] .name {
    color: var(--accent-ink, #78350f);
    font-weight: 600;
  }
  .initials {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 3.5rem;
    height: 3.5rem;
    border-radius: var(--radius-full, 999px);
    background: var(--surface-2, #f4f0e8);
    font-size: 1.1rem;
    font-weight: 650;
  }
  /* Always rendered so a save's outcome never reflows the grid; empty most of the time. */
  .status {
    min-height: 1.2rem;
    margin: 0.6rem 0 0;
    color: var(--muted, #5d6570);
    font-size: var(--text-sm, 0.875rem);
  }
  @media (prefers-reduced-motion: reduce) {
    .pick {
      transition: none;
    }
  }
</style>
