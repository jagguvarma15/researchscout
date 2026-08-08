<script lang="ts">
  // "Newest paper N hours old" in the footer - the freshness fact the deployment outage
  // taught us to want on the page rather than only in a log. Best-effort, but never mute:
  // a failure renders a muted "unavailable" line, because an absent fact and an
  // unreachable API should not look identical.
  let line = $state('');
  let failed = $state(false);

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
      .then((res) => {
        if (!res.ok) throw new Error(String(res.status));
        return res.json();
      })
      .then((status) => {
        if (!cancelled && status?.newest_paper_at) line = describe(status.newest_paper_at);
      })
      .catch(() => {
        if (!cancelled) failed = true;
      });
    return () => {
      cancelled = true;
    };
  });
</script>

{#if line}
  <p class="freshness" role="status">{line}</p>
{:else if failed}
  <p class="freshness" role="status">Freshness unknown - the status endpoint is unreachable.</p>
{/if}

<style>
  .freshness {
    margin: 0.4rem 0 0;
    font-size: 0.8rem;
    color: var(--muted);
  }
</style>
