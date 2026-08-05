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

// Returns the unlock function; call it on close so nesting restores the previous value.
export function lockBodyScroll(): () => void {
  const previous = document.body.style.overflow;
  document.body.style.overflow = 'hidden';
  return () => {
    document.body.style.overflow = previous;
  };
}
