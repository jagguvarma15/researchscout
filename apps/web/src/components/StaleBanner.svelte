<script lang="ts">
  // Says so when the page you are looking at is not fresh.
  //
  // Public pages are cached at the edge with a long stale-while-revalidate window, so when the
  // backend is asleep the site still reads - it just reads as of some earlier moment. The
  // server stamps when it rendered; if that is well in the past by the time the page loads,
  // the edge served a saved copy because it could not reach the origin.
  //
  // Checked once, at mount, deliberately: on a timer it would also fire on a tab left open for
  // an hour, which is not the same thing and not worth alarming anyone about.

  let { renderedAt }: { renderedAt: string } = $props();

  const STALE_AFTER_MS = 5 * 60 * 1000;

  let staleSince = $state<string | null>(null);

  $effect(() => {
    const rendered = Date.parse(renderedAt);
    if (Number.isNaN(rendered)) return;
    if (Date.now() - rendered <= STALE_AFTER_MS) return;
    staleSince = new Date(rendered).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    });
  });

  function retry(): void {
    // A fresh query string skips the cached copy, so this reaches the origin if it is back.
    const url = new URL(window.location.href);
    url.searchParams.set('r', String(Date.now()));
    window.location.replace(url.toString());
  }
</script>

{#if staleSince}
  <div class="stale" role="status">
    <span>
      Showing a saved copy from {staleSince}. The live index could not be reached just now.
    </span>
    <button type="button" onclick={retry}>Try again</button>
  </div>
{/if}

<style>
  .stale {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
    padding: 0.5rem 1rem;
    background: var(--accent-soft);
    color: var(--accent-ink);
    font-size: var(--text-sm);
    text-align: center;
  }
  button {
    padding: 0.15rem 0.7rem;
    border: 1px solid currentColor;
    border-radius: var(--radius-full);
    background: transparent;
    color: inherit;
    font: inherit;
    font-size: var(--text-xs);
    cursor: pointer;
  }
  button:hover {
    background: color-mix(in srgb, currentColor 12%, transparent);
  }
</style>
