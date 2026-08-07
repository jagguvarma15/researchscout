// Marks <html data-paging="newer|older"> for the life of a pager navigation, so global.css
// can slide the root in the travel direction and index.astro can suppress the per-title
// morph names (which would otherwise pin every title in place while the page slides).
//
// Three moments matter. Before preparation: the attribute must already be present when the
// browser captures the OLD snapshot. After the swap: astro's swapRootAttributes removes
// every custom attribute from <html>, so the overridable event.swap is wrapped to put the
// attribute back for the NEW snapshot. Finished: only then may it go - removing it on
// astro:page-load would strip a live animation, because that event fires when the swap
// completes, not when the transition does.

import type {
  TransitionBeforePreparationEvent,
  TransitionBeforeSwapEvent,
} from 'astro:transitions/client';

function directionOf(source: Element | undefined): string | undefined {
  const dir = source?.closest<HTMLElement>('[data-page-dir]')?.dataset.pageDir;
  return dir === 'newer' || dir === 'older' ? dir : undefined;
}

export function initNavDirection(): void {
  document.addEventListener('astro:before-preparation', (event) => {
    const dir = directionOf((event as TransitionBeforePreparationEvent).sourceElement);
    if (dir) document.documentElement.dataset.paging = dir;
    else delete document.documentElement.dataset.paging;
  });
  document.addEventListener('astro:before-swap', (event) => {
    const swapEvent = event as TransitionBeforeSwapEvent;
    const dir = directionOf(swapEvent.sourceElement);
    if (!dir) return;
    const swap = swapEvent.swap;
    swapEvent.swap = () => {
      swap();
      document.documentElement.dataset.paging = dir;
    };
    const clear = () => delete document.documentElement.dataset.paging;
    // Not .finally: a skipped transition can reject finished, and the rejection must not
    // surface as unhandled just to clean an attribute up.
    swapEvent.viewTransition.finished.then(clear, clear);
  });
}
