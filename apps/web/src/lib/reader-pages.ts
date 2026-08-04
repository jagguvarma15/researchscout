// Which pages of a PDF are worth having on screen.
//
// The reader scrolls continuously through the whole document, but a canvas per page would
// hold the entire paper as bitmaps - on a 40-page paper at fit-width that is hundreds of
// megabytes, and this runs on an 8GB machine. So every page occupies its true height from
// the moment the document opens (an honest scrollbar, and no content shifting under you),
// while only the pages near the viewport actually carry a canvas.
//
// The arithmetic lives here, apart from the component, because it is the part that has
// edges worth testing: the first page, the last page, and the moment one page hands over
// to the next.

export interface PageBox {
  /** Distance from the top of the scroll content to the top of this page, in CSS pixels. */
  top: number;
  height: number;
}

export interface PageRange {
  first: number;
  last: number;
}

/** Stack pages top to bottom with a fixed gap between them. Indices are zero-based. */
export function layoutPages(heights: number[], gap: number): PageBox[] {
  const boxes: PageBox[] = [];
  let top = 0;
  for (const height of heights) {
    boxes.push({ top, height });
    top += height + gap;
  }
  return boxes;
}

/**
 * Total height of the scroll content, so the container can size itself before a single page
 * has rendered. The trailing gap is deliberately absent: it would be blank space hanging
 * below the last page.
 */
export function totalHeight(boxes: PageBox[]): number {
  if (boxes.length === 0) return 0;
  const last = boxes[boxes.length - 1];
  return last.top + last.height;
}

/**
 * The pages to keep rendered: everything touching the viewport, plus `overscan` either side
 * so a scroll of one page does not begin with a blank.
 *
 * Returns an inclusive range clamped to the document. An empty document gives {first: 0,
 * last: -1}, which every `for` loop over it treats as nothing to do.
 */
export function visibleRange(
  boxes: PageBox[],
  scrollTop: number,
  viewportHeight: number,
  overscan = 1,
): PageRange {
  if (boxes.length === 0) return { first: 0, last: -1 };
  const top = scrollTop;
  const bottom = scrollTop + viewportHeight;
  let first = -1;
  let last = -1;
  for (let i = 0; i < boxes.length; i += 1) {
    const box = boxes[i];
    // Touching, not merely containing: a page half off the top edge is still on screen.
    if (box.top < bottom && box.top + box.height > top) {
      if (first === -1) first = i;
      last = i;
    }
  }
  // Scrolled past the end (or before the start) of every page - keep the nearest one, so
  // the reader is never showing nothing at all.
  if (first === -1) {
    const nearest = scrollTop <= 0 ? 0 : boxes.length - 1;
    first = nearest;
    last = nearest;
  }
  return {
    first: Math.max(0, first - overscan),
    last: Math.min(boxes.length - 1, last + overscan),
  };
}

/**
 * The page the reader is on: the one showing the most of itself. Ties go to the earlier
 * page, so scrolling forward changes the number only once the next page genuinely leads.
 *
 * Returned one-based, because that is what a page number means to a reader.
 */
export function currentPage(boxes: PageBox[], scrollTop: number, viewportHeight: number): number {
  if (boxes.length === 0) return 1;
  let best = 0;
  let bestVisible = -1;
  for (let i = 0; i < boxes.length; i += 1) {
    const box = boxes[i];
    const visible =
      Math.min(box.top + box.height, scrollTop + viewportHeight) - Math.max(box.top, scrollTop);
    if (visible > bestVisible) {
      bestVisible = visible;
      best = i;
    }
  }
  return best + 1;
}

/** Where to scroll so a given one-based page sits at the top of the viewport. */
export function scrollTopFor(boxes: PageBox[], page: number): number {
  if (boxes.length === 0) return 0;
  const index = Math.min(Math.max(1, page), boxes.length) - 1;
  return boxes[index].top;
}
