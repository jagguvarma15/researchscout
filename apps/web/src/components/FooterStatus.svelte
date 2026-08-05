<script lang="ts">
  // "Newest paper N hours old" in the footer - the freshness fact the deployment outage
  // taught us to want on the page rather than only in a log. Best-effort: fetched lazily
  // through the proxy, and any failure renders nothing rather than an error.
  let line = $state('');

  function describe(newest: string): string {
    const hours = (Date.now() - new Date(newest).getTime()) / 3_600_000;
    if (!Number.isFinite(hours) || hours < 0) return '';
    if (hours < 1) return 'Newest paper: under an hour old';
    if (hours < 48) {
      const whole = Math.floor(hours);
      return `Newest paper: ${whole} hour${whole === 1 ? '' : 's'} old`;
    }
    const days = Math.floor(hours / 24);
    return `Newest paper: ${days} day${days === 1 ? '' : 's'} old`;
  }

  $effect(() => {
    let cancelled = false;
    fetch('/api/system/status')
      .then((res) => (res.ok ? res.json() : null))
      .then((status) => {
        if (!cancelled && status?.newest_paper_at) line = describe(status.newest_paper_at);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  });
</script>

{#if line}
  <p class="freshness">{line}</p>
{/if}

<style>
  .freshness {
    margin: 0.4rem 0 0;
    font-size: 0.8rem;
    color: var(--muted);
  }
</style>
