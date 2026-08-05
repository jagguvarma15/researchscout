<script lang="ts">
  // One invisible island per page that turns markup annotations into implicit-feedback
  // events: cards carry data-paper-id/data-rank, impressions fire once per card at half
  // visibility, link clicks map to click/open_pdf, and [data-dismiss] buttons log an explicit
  // negative and move their card to the end of its list. With a paperId prop (detail page) it
  // also measures dwell and reports it on leave when past the threshold.
  //
  // Dismiss takes the row out of the feed and the account remembers it, so a reload does not
  // put it back. Out of the feed only: the API applies the exclusion to the recency listing and
  // to nothing else, so the paper is still searchable and its own page still opens.
  //
  // Undo is why the removal is done carefully rather than with remove(): the card and the
  // sibling it sat in front of are kept, so putting it back puts it back where it was rather
  // than at the end. DismissNotice owns the dialog and calls undo() through the event detail.

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

  /**
   * Take a card out of the feed, and hand back the function that puts it back where it was.
   *
   * A day heading with nothing under it is a lie about the timeline, so an emptied list takes
   * its whole section with it - and undo restores that too.
   */
  function remove(card: HTMLElement): () => void {
    const list = card.parentElement;
    const before = card.nextElementSibling;
    const section = list?.closest<HTMLElement>('.day');
    const wasOnlyCard = list?.children.length === 1;

    card.classList.add('going');
    card.remove();
    if (wasOnlyCard && section) section.hidden = true;

    return () => {
      if (list) list.insertBefore(card, before);
      card.classList.remove('going');
      if (section) section.hidden = false;
    };
  }

  /**
   * Tell the account, so the dismissal survives a reload.
   *
   * Best effort by design: this is a cache, the page has already removed the card, and a
   * signed-out visitor gets a 401 that costs nothing. Never awaited - the reader is not
   * waiting on it.
   */
  function rememberDismissal(paperId: string, method: 'POST' | 'DELETE') {
    if (!enabled) return;
    const url =
      method === 'POST'
        ? '/api/me/dismissals'
        : `/api/me/dismissals?paper_id=${encodeURIComponent(paperId)}`;
    void fetch(url, {
      method,
      headers: { 'content-type': 'application/json' },
      body: method === 'POST' ? JSON.stringify({ paper_id: paperId }) : undefined,
      keepalive: true,
    }).catch(() => undefined);
  }

  function onDocumentClick(event: MouseEvent) {
    const card = cardOf(event.target);
    const paperId = card?.dataset.paperId;
    if (!card || !paperId) return;
    const dismiss = (event.target as Element).closest('[data-dismiss]');
    if (dismiss) {
      event.preventDefault();
      logEvent({ event: 'dismiss', paper_id: paperId, rank: rankOf(card), surface });
      // An explicit negative is rare and valuable: send it now, before anything moves.
      flushEvents();

      const title = card.querySelector('.title')?.textContent?.trim() ?? '';
      const restore = remove(card);
      rememberDismissal(paperId, 'POST');
      // Removing the card takes the focused button out of the document with it, which would
      // drop a keyboard user on the body having lost their place. The dialog takes focus next,
      // and hands it back to the feed when it closes.
      document.dispatchEvent(
        new CustomEvent('rs:dismissed', {
          detail: {
            title,
            // The dismiss event stays in the log: it happened, and the log is a history of
            // what people did rather than a statement of where things ended up. Where things
            // ended up is the account's dismissal list, which the DELETE corrects.
            undo: () => {
              restore();
              rememberDismissal(paperId, 'DELETE');
            },
          },
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

    // Opening a paper is what "recently opened" means, so it is recorded here rather than when
    // the link was clicked: arriving from a search, a digest or a bookmark all count, and a
    // click that never finished loading does not.
    if (paperId) {
      void fetch('/api/me/recent', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ paper_id: paperId }),
        keepalive: true,
      }).catch(() => undefined);
    }

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
