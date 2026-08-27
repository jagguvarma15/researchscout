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
// The stored shape: { v, savedAt, items }. Bare arrays predate the envelope and still load;
// the next save rewrites them, so old papers migrate the moment they are touched again.
const VERSION = 1;
// One key per paper with no other bound would grow monotonically with reading history until
// the origin quota refuses every save. The sweep below drops papers not highlighted in half
// a year, then everything beyond the newest fifty - generous enough that an active reader
// never notices, small enough that the quota stays out of reach.
const MAX_AGE_MS = 180 * 24 * 60 * 60 * 1000;
const MAX_PAPERS = 50;
const MAX_ITEMS = 200;
const MAX_RECTS = 50;

function key(paperId: string): string {
  return `${PREFIX}${paperId}`;
}

function cleanRect(value: unknown): HighlightRect | null {
  const rect = value as Partial<HighlightRect> | null;
  if (typeof rect !== 'object' || rect === null) return null;
  const { x, y, w, h } = rect;
  for (const n of [x, y, w, h]) {
    if (typeof n !== 'number' || !Number.isFinite(n)) return null;
  }
  return { x: x as number, y: y as number, w: w as number, h: h as number };
}

// Stored values are untrusted input the way the chat transcript's are: the shape filter
// caps every field so nothing pathological (hand-edited or half-written) reaches the
// reader's rendering or its per-item text listing.
function cleanHighlight(value: unknown): Highlight | null {
  const item = value as Partial<Highlight> | null;
  if (typeof item !== 'object' || item === null) return null;
  if (typeof item.id !== 'string' || item.id.length === 0 || item.id.length > 64) return null;
  if (typeof item.page !== 'number' || !Number.isInteger(item.page)) return null;
  if (item.page < 1 || item.page > 10_000) return null;
  if (typeof item.color !== 'string' || item.color.length > 32) return null;
  if (typeof item.text !== 'string' || item.text.length > 2_000) return null;
  if (!Array.isArray(item.rects)) return null;
  const rects = item.rects
    .slice(0, MAX_RECTS)
    .map(cleanRect)
    .filter((rect): rect is HighlightRect => rect !== null);
  return { id: item.id, page: item.page, color: item.color, text: item.text, rects };
}

function cleanItems(value: unknown): Highlight[] {
  if (!Array.isArray(value)) return [];
  return value
    .slice(0, MAX_ITEMS)
    .map(cleanHighlight)
    .filter((item): item is Highlight => item !== null);
}

function parseStored(raw: string): { savedAt: number; items: Highlight[] } | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  // The pre-envelope shape: a bare array, age unknown - treated as saved just now, so a
  // legacy paper gets a full grace period rather than being swept on sight.
  if (Array.isArray(parsed)) return { savedAt: Date.now(), items: cleanItems(parsed) };
  if (typeof parsed !== 'object' || parsed === null) return null;
  const envelope = parsed as { v?: unknown; savedAt?: unknown; items?: unknown };
  if (envelope.v !== VERSION) return null;
  const savedAt = typeof envelope.savedAt === 'number' ? envelope.savedAt : Date.now();
  return { savedAt, items: cleanItems(envelope.items) };
}

/**
 * Everything highlighted on this paper, or nothing at all.
 *
 * Storage is allowed to fail - private browsing refuses it, and a half-written value from an
 * interrupted save is not worth a crash in a PDF reader. Either way the answer is an empty
 * list and reading carries on.
 */
export function loadHighlights(paperId: string): Highlight[] {
  sweepOnce();
  try {
    const raw = localStorage.getItem(key(paperId));
    if (!raw) return [];
    return parseStored(raw)?.items ?? [];
  } catch {
    return [];
  }
}

/** Returns whether the write landed, so the reader can say so rather than silently lose it. */
export function saveHighlights(paperId: string, items: Highlight[]): boolean {
  sweepOnce();
  try {
    if (items.length === 0) localStorage.removeItem(key(paperId));
    else {
      localStorage.setItem(
        key(paperId),
        JSON.stringify({ v: VERSION, savedAt: Date.now(), items: items.slice(0, MAX_ITEMS) }),
      );
    }
    return true;
  } catch {
    return false;
  }
}

let swept = false;

function sweepOnce(): void {
  if (swept) return;
  swept = true;
  sweepHighlights();
}

/**
 * Drop highlight keys nobody will come back for: papers untouched for half a year, then
 * everything beyond the newest fifty. Runs once per page load, lazily, from the first
 * storage call - a visitor who never opens the reader pays nothing.
 */
export function sweepHighlights(): void {
  let keys: string[];
  try {
    if (typeof localStorage.length !== 'number' || typeof localStorage.key !== 'function') {
      return;
    }
    keys = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const name = localStorage.key(i);
      if (name?.startsWith(PREFIX)) keys.push(name);
    }
  } catch {
    return;
  }
  const kept: { name: string; savedAt: number }[] = [];
  for (const name of keys) {
    try {
      const stored = parseStored(localStorage.getItem(name) ?? '');
      if (stored === null || stored.items.length === 0) {
        localStorage.removeItem(name);
        continue;
      }
      if (Date.now() - stored.savedAt > MAX_AGE_MS) {
        localStorage.removeItem(name);
        continue;
      }
      kept.push({ name, savedAt: stored.savedAt });
    } catch {
      // One unreadable key must not stop the sweep of the rest.
    }
  }
  kept.sort((a, b) => b.savedAt - a.savedAt);
  for (const { name } of kept.slice(MAX_PAPERS)) {
    try {
      localStorage.removeItem(name);
    } catch {
      // Same stance: best effort.
    }
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
