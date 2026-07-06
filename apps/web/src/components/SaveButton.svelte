<script lang="ts">
  // Star toggle for the reading list. Goes through the authenticated proxy; signed-out
  // visitors are sent to login instead of seeing a dead button.

  let {
    paperId,
    saved: initial,
    authenticated,
  }: { paperId: string; saved: boolean; authenticated: boolean } = $props();

  let saved = $state(initial);
  let busy = $state(false);

  async function toggle() {
    if (!authenticated) {
      window.location.href = `/auth/login?returnTo=${encodeURIComponent(window.location.pathname)}`;
      return;
    }
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
  class="star"
  class:saved
  onclick={toggle}
  disabled={busy}
  aria-pressed={saved}
  title={saved ? 'Remove from reading list' : 'Save to reading list'}
>
  {saved ? '★' : '☆'}
</button>

<style>
  .star {
    border: none;
    background: none;
    font-size: 1.15rem;
    line-height: 1;
    cursor: pointer;
    color: var(--muted, #6a7076);
    padding: 0 0.2rem;
  }
  .star.saved {
    color: #e8a13c;
  }
</style>
