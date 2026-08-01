<script lang="ts">
  // One invisible island per page that turns markup annotations into implicit-feedback
  // events: cards carry data-paper-id/data-rank, impressions fire once per card at half
  // visibility, link clicks map to click/open_pdf, and [data-dismiss] buttons log an explicit
  // negative and hide their card. With a paperId prop (detail page) it also measures dwell
  // and reports it on leave when past the threshold.

  import { onMount } from 'svelte';

  import { flushEvents, logEvent } from '../lib/events';

  let {
    surface,
    paperId = null,
    enabled = true,
  }: { surface: string; paperId?: string | null; enabled?: boolean } = $props();

  const DWELL_THRESHOLD_MS = 20_000;

  function cardOf(target: EventTarget | null): HTMLElement | null {
    return target instanceof Element ? target.closest<HTMLElement>('[data-paper-id]') : null;
  }

  function rankOf(card: HTMLElement): number | undefined {
    const raw = card.dataset.rank;
    const rank = raw === undefined ? Number.NaN : Number(raw);
    return Number.isInteger(rank) ? rank : undefined;
  }

  function onDocumentClick(event: MouseEvent) {
    const card = cardOf(event.target);
    if (!card || !card.dataset.paperId) return;
    const dismiss = (event.target as Element).closest('[data-dismiss]');
    if (dismiss) {
      event.preventDefault();
      logEvent({
        event: 'dismiss',
        paper_id: card.dataset.paperId,
        rank: rankOf(card),
        surface,
      });
      // An explicit negative is rare and valuable: send it now, then hide the card.
      flushEvents();
      card.remove();
      return;
    }
    const link = (event.target as Element).closest('a');
    if (link) {
      logEvent({
        event: link.closest('.quick') ? 'open_pdf' : 'click',
        paper_id: card.dataset.paperId,
        rank: rankOf(card),
        surface,
      });
    }
  }

  onMount(() => {
    // Nothing to record for a signed-out visitor: reading signals belong to an account, the
    // events route requires one, and beacons that 401 are just noise in the log.
    if (!enabled) return;

    const seen = new WeakSet<Element>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting || seen.has(entry.target)) continue;
          seen.add(entry.target);
          observer.unobserve(entry.target);
          const card = entry.target as HTMLElement;
          if (card.dataset.paperId) {
            logEvent({
              event: 'impression',
              paper_id: card.dataset.paperId,
              rank: rankOf(card),
              surface,
            });
          }
        }
      },
      { threshold: 0.5 },
    );
    for (const card of document.querySelectorAll('[data-paper-id]')) observer.observe(card);

    let dwellStart = document.visibilityState === 'visible' ? performance.now() : null;
    let dwellTotal = 0;

    function pauseDwell() {
      if (dwellStart !== null) {
        dwellTotal += performance.now() - dwellStart;
        dwellStart = null;
      }
    }

    function onVisibility() {
      if (document.visibilityState === 'visible') dwellStart ??= performance.now();
      else pauseDwell();
    }

    function onPageHide() {
      if (!paperId) return;
      pauseDwell();
      if (dwellTotal >= DWELL_THRESHOLD_MS) {
        logEvent({ event: 'dwell', paper_id: paperId, value: Math.round(dwellTotal), surface });
        flushEvents();
      }
    }

    if (paperId) {
      document.addEventListener('visibilitychange', onVisibility);
      addEventListener('pagehide', onPageHide);
    }
    return () => {
      observer.disconnect();
      if (paperId) {
        document.removeEventListener('visibilitychange', onVisibility);
        removeEventListener('pagehide', onPageHide);
      }
    };
  });
</script>

<svelte:document onclick={onDocumentClick} />
