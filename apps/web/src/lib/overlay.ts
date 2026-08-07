// Shared overlay behavior for full-screen surfaces (filter sidebar, PDF reader): a focus trap
// and a body scroll lock. The command palette predates these and does neither; new overlays do.

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Keep Tab cycling inside the container; call from the container's keydown handler.
export function trapFocus(container: HTMLElement, event: KeyboardEvent): void {
  if (event.key !== 'Tab') return;
  const nodes = [...container.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
  if (nodes.length === 0) return;
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

/**
 * Whether a click landed outside `root`, judged by the event's composed path.
 *
 * The path is snapshotted at dispatch, so a row that a framework re-rendered away between
 * the row's own handler and a document-level one still counts as inside. The tempting
 * alternative — `root.contains(event.target)` at handler time — reads a detached node in
 * that window and reports "outside" for the very element that was clicked, which is how the
 * omnibox used to close itself in answer to its own Ask row.
 */
export function clickedOutside(event: Event, root: Element | undefined): boolean {
  if (!root) return false;
  return !event.composedPath().includes(root);
}

// One shared lock count across every overlay. The naive per-caller save/restore stranded the
// lock when two overlays overlapped: the second captured "hidden" as its previous value and
// restored it, leaving the page unscrollable after both closed. With a count, the styles are
// written once by the first acquire and removed only by the last release.
let lockCount = 0;
let savedRootOverflow = '';
let savedBodyOverflow = '';
let savedOverscroll = '';

// Returns the unlock function; each is idempotent, and overlapping overlays nest safely.
// Both html and body are locked - iOS ignores overflow on the body alone for touch drags,
// which is how a page kept scrolling behind the open drawer - and overscroll-behavior stops
// the rubber-band from reaching the page underneath. Deliberately not the fixed-body
// technique: that settles sticky elements at their in-flow offset, which would slide the
// header (and the search field in it) off-screen whenever a lock lands mid-page.
export function lockBodyScroll(): () => void {
  if (lockCount === 0) {
    const root = document.documentElement;
    savedRootOverflow = root.style.overflow;
    savedBodyOverflow = document.body.style.overflow;
    savedOverscroll = root.style.overscrollBehavior;
    root.style.overflow = 'hidden';
    document.body.style.overflow = 'hidden';
    root.style.overscrollBehavior = 'none';
  }
  lockCount += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    lockCount -= 1;
    if (lockCount === 0) {
      const root = document.documentElement;
      root.style.overflow = savedRootOverflow;
      document.body.style.overflow = savedBodyOverflow;
      root.style.overscrollBehavior = savedOverscroll;
    }
  };
}
