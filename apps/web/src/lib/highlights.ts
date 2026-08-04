// Highlights you make while reading a paper.
//
// They live in the browser, under a key per paper. That is a deliberate limit: highlights
// never leave the machine that made them, so they are not personal data anyone here has to
// hold, export or delete on request - and they work signed out. The cost is that they do not
// follow you to another device, which the reader says plainly.
//
// Rectangles are stored in *unscaled* PDF units, divided by the render scale on the way in
// and multiplied by it on the way out. Storing screen pixels instead would pin a highlight
// to the zoom level and window width it was made at, and it would slide off its words the
// first time either changed.

export interface HighlightRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Highlight {
  id: string;
  /** One-based, matching what the reader shows. */
  page: number;
  color: string;
  /** The selected text, so a highlight can be listed and found without rendering the page. */
  text: string;
  rects: HighlightRect[];
}

/** The shape of a DOMRect, narrowed to what this module needs and can be handed in tests. */
export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

const PREFIX = 'rs-highlights:';

function key(paperId: string): string {
  return `${PREFIX}${paperId}`;
}

/**
 * Everything highlighted on this paper, or nothing at all.
 *
 * Storage is allowed to fail - private browsing refuses it, and a half-written value from an
 * interrupted save is not worth a crash in a PDF reader. Either way the answer is an empty
 * list and reading carries on.
 */
export function loadHighlights(paperId: string): Highlight[] {
  try {
    const raw = localStorage.getItem(key(paperId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is Highlight =>
        typeof item?.id === 'string' &&
        typeof item?.page === 'number' &&
        Array.isArray(item?.rects),
    );
  } catch {
    return [];
  }
}

/** Returns whether the write landed, so the reader can say so rather than silently lose it. */
export function saveHighlights(paperId: string, items: Highlight[]): boolean {
  try {
    if (items.length === 0) localStorage.removeItem(key(paperId));
    else localStorage.setItem(key(paperId), JSON.stringify(items));
    return true;
  } catch {
    return false;
  }
}

/**
 * Screen rectangles from a text selection, converted to page coordinates at scale 1.
 *
 * `page` is the rendered page's own box, so the result is relative to the top left corner of
 * the page rather than the window, and survives scrolling. Zero-area rectangles are dropped:
 * a selection produces one per line plus empty ones at its edges.
 */
export function toPageRects(selection: Box[], page: Box, scale: number): HighlightRect[] {
  if (scale <= 0) return [];
  return selection
    .filter((rect) => rect.width > 0.5 && rect.height > 0.5)
    .map((rect) => ({
      x: (rect.left - page.left) / scale,
      y: (rect.top - page.top) / scale,
      w: rect.width / scale,
      h: rect.height / scale,
    }));
}

/** Page coordinates back to pixels for painting at the current scale. */
export function toScreenRects(
  rects: HighlightRect[],
  scale: number,
): { left: number; top: number; width: number; height: number }[] {
  return rects.map((rect) => ({
    left: rect.x * scale,
    top: rect.y * scale,
    width: rect.w * scale,
    height: rect.h * scale,
  }));
}

/** Which page a screen rectangle belongs to, so a selection across a page break splits. */
export function pageOfRect(rect: Box, pages: { page: number; box: Box }[]): number | null {
  const midX = rect.left + rect.width / 2;
  const midY = rect.top + rect.height / 2;
  for (const candidate of pages) {
    const { box } = candidate;
    if (
      midX >= box.left &&
      midX <= box.left + box.width &&
      midY >= box.top &&
      midY <= box.top + box.height
    ) {
      return candidate.page;
    }
  }
  return null;
}

/** The highlight under a point on a page, newest first so overlaps remove in reverse order. */
export function highlightAt(
  items: Highlight[],
  page: number,
  x: number,
  y: number,
  scale: number,
): Highlight | null {
  for (let i = items.length - 1; i >= 0; i -= 1) {
    const item = items[i];
    if (item.page !== page) continue;
    for (const rect of toScreenRects(item.rects, scale)) {
      if (
        x >= rect.left &&
        x <= rect.left + rect.width &&
        y >= rect.top &&
        y <= rect.top + rect.height
      ) {
        return item;
      }
    }
  }
  return null;
}

/**
 * An identifier for a new highlight.
 *
 * Deliberately not crypto.randomUUID: this only has to be unique within one paper's list in
 * one browser, and a counter salted by the clock is enough for that without requiring a
 * secure context.
 */
let counter = 0;
export function newId(now: number): string {
  counter += 1;
  return `${now.toString(36)}-${counter.toString(36)}`;
}
