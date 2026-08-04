<script lang="ts">
  // One invisible island per page that turns markup annotations into implicit-feedback
  // events: cards carry data-paper-id/data-rank, impressions fire once per card at half
  // visibility, link clicks map to click/open_pdf, and [data-dismiss] buttons log an explicit
  // negative and move their card to the end of its list. With a paperId prop (detail page) it
  // also measures dwell and reports it on leave when past the threshold.
  //
  // Dismiss used to call card.remove(). It now demotes instead: the card goes to the bottom of
  // its list and the account remembers it, so the next visit puts it there too. A paper you are
  // not interested in today is not one you should be unable to find tomorrow.

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

  /** Move a card to the end of its own list, dimmed, keeping the day headings honest. */
  function demote(card: HTMLElement) {
    card.classList.add('dismissed');
    card.parentElement?.append(card);
  }

  /**
   * Tell the account, so the demotion survives a reload.
   *
   * Best effort by design: this is a cache, the page has already moved the card, and a signed-out
   * visitor gets a 401 that costs nothing. Never awaited - the reader is not waiting on it.
   */
  function rememberDismissal(paperId: string | undefined) {
    if (!paperId || !enabled) return;
    void fetch('/api/me/dismissals', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ paper_id: paperId }),
      keepalive: true,
    }).catch(() => undefined);
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
      // An explicit negative is rare and valuable: send it now, before anything moves.
      flushEvents();
      // Moving an element with append() takes it out of the document and puts it back, which
      // blurs whatever was focused inside it. Keyboard users would land on the body and lose
      // their place, so the button is focused again before anything reads activeElement.
      const hadFocus = document.activeElement === dismiss;
      demote(card);
      if (hadFocus && dismiss instanceof HTMLElement) dismiss.focus();
      rememberDismissal(card.dataset.paperId);
      document.dispatchEvent(
        new CustomEvent('rs:dismissed', {
          detail: { title: card.querySelector('.title')?.textContent?.trim() ?? '' },
        }),
      );
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
