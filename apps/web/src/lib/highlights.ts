// Highlights you make while reading a paper.
//
// One mechanism: drag a box over anything. It does not matter whether what is underneath is a
// sentence, a displayed equation, a symbol or a figure - the box tightens onto whatever ink is
// inside it and that becomes the mark. Selecting text with the cursor was the obvious first
// answer and it was the wrong one: pdf.js lays a PDF out as one absolutely-positioned span per
// glyph run, so dragging through them is jumpy, `getClientRects()` comes back as dozens of
// little boxes at different heights, and a displayed equation has no selectable text at all.
//
// They live in the browser, under a key per paper. That is a deliberate limit: highlights never
// leave the machine that made them, so they are not personal data anyone here has to hold,
// export or delete on request - and they work signed out. The cost is that they do not follow
// you to another device, which the reader says plainly.
//
// Rectangles are stored in *unscaled* PDF units, divided by the render scale on the way in and
// multiplied by it on the way out. Storing screen pixels instead would pin a highlight to the
// zoom level and window width it was made at, and it would slide off its words the first time
// either changed.

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
  /** Whatever text fell inside the box, so a mark can be listed without rendering the page. */
  text: string;
  rects: HighlightRect[];
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

/** Page coordinates to pixels for painting at the current scale. */
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

/**
 * The rectangle between two dragged points, in page units.
 *
 * Dragging up or to the left is just as natural as down and right, so the corners are sorted
 * rather than assumed: a rectangle with negative width paints nothing and hit-tests as empty.
 */
export function rectFromDrag(
  from: { x: number; y: number },
  to: { x: number; y: number },
): HighlightRect {
  return {
    x: Math.min(from.x, to.x),
    y: Math.min(from.y, to.y),
    w: Math.abs(to.x - from.x),
    h: Math.abs(to.y - from.y),
  };
}

/** Keeps a dragged rectangle on its page, so a highlight cannot hang off the paper. */
export function clampRect(rect: HighlightRect, width: number, height: number): HighlightRect {
  const x = Math.min(Math.max(0, rect.x), width);
  const y = Math.min(Math.max(0, rect.y), height);
  return { x, y, w: Math.min(rect.w, width - x), h: Math.min(rect.h, height - y) };
}

/**
 * The bounding box of everything drawn inside a patch of a rendered page.
 *
 * This is what lets a loose drag become a tidy mark: the reader hands over the pixels the box
 * covers, and this finds where the ink actually is. It reads the rendered canvas rather than
 * the text layer on purpose - the canvas is where a formula, a plot and a paragraph all end up
 * looking the same, so one rule covers every kind of thing on the page.
 *
 * Coordinates are in the pixels handed in, relative to the patch. Null means the box was empty,
 * which the reader treats as nothing worth marking.
 *
 * `threshold` is a channel value: a PDF page is painted on white, so anything below it on any
 * channel is ink. Left a little under 255 so that antialiasing and off-white paper do not
 * register as content.
 */
export function inkBounds(
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  threshold = 244,
): { x: number; y: number; w: number; h: number } | null {
  let minX = width;
  let minY = height;
  let maxX = -1;
  let maxY = -1;
  for (let y = 0; y < height; y += 1) {
    const row = y * width * 4;
    for (let x = 0; x < width; x += 1) {
      const i = row + x * 4;
      // Transparent pixels are paper too: nothing was drawn there.
      if (pixels[i + 3] < 8) continue;
      if (pixels[i] >= threshold && pixels[i + 1] >= threshold && pixels[i + 2] >= threshold) {
        continue;
      }
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }
  if (maxX < 0) return null;
  return { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
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
