// A highlight has to stay on its words. These pin the property that makes that true - that
// rectangles are stored independently of the zoom they were made at - plus the storage
// failures a reader must survive rather than crash on.

import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  highlightAt,
  loadHighlights,
  newId,
  pageOfRect,
  saveHighlights,
  toPageRects,
  toScreenRects,
  type Highlight,
} from './highlights';

const PAPER = 'arxiv:2401.00001';

// A storage of our own rather than the environment's. Node 22 defines a `localStorage` global
// that throws unless the process was started with --localstorage-file, and it shadows the one
// jsdom provides - so a test touching the bare global is testing the runner, not this module.
let store: Map<string, string>;

function memoryStorage() {
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  };
}

beforeEach(() => {
  store = new Map();
  vi.stubGlobal('localStorage', memoryStorage());
});

describe('coordinates', () => {
  const page = { left: 100, top: 200, width: 800, height: 1000 };

  it('stores a selection relative to its page, at scale 1', () => {
    const rects = toPageRects([{ left: 150, top: 250, width: 60, height: 12 }], page, 2);
    expect(rects).toEqual([{ x: 25, y: 25, w: 30, h: 6 }]);
  });

  it('survives a change of zoom', () => {
    // Made at 2x, painted at 3x: the highlight lands where the same words now are.
    const stored = toPageRects([{ left: 150, top: 250, width: 60, height: 12 }], page, 2);
    expect(toScreenRects(stored, 3)).toEqual([{ left: 75, top: 75, width: 90, height: 18 }]);
    // And painting at the scale it was made at returns the original offsets.
    expect(toScreenRects(stored, 2)).toEqual([{ left: 50, top: 50, width: 60, height: 12 }]);
  });

  it('drops the empty rectangles a selection produces at its edges', () => {
    const rects = toPageRects(
      [
        { left: 150, top: 250, width: 0, height: 12 },
        { left: 150, top: 250, width: 60, height: 12 },
        { left: 150, top: 262, width: 60, height: 0 },
      ],
      page,
      1,
    );
    expect(rects).toHaveLength(1);
  });

  it('refuses a nonsense scale rather than dividing by zero', () => {
    expect(toPageRects([{ left: 1, top: 1, width: 1, height: 1 }], page, 0)).toEqual([]);
  });
});

describe('pageOfRect', () => {
  const pages = [
    { page: 1, box: { left: 0, top: 0, width: 100, height: 100 } },
    { page: 2, box: { left: 0, top: 110, width: 100, height: 100 } },
  ];

  it('assigns each rectangle to the page it sits on', () => {
    expect(pageOfRect({ left: 10, top: 10, width: 20, height: 10 }, pages)).toBe(1);
    expect(pageOfRect({ left: 10, top: 150, width: 20, height: 10 }, pages)).toBe(2);
  });

  it('returns nothing for a rectangle in the gap between pages', () => {
    expect(pageOfRect({ left: 10, top: 102, width: 20, height: 4 }, pages)).toBeNull();
  });
});

describe('storage', () => {
  const one: Highlight = {
    id: 'a',
    page: 2,
    color: 'yellow',
    text: 'attention',
    rects: [{ x: 1, y: 2, w: 3, h: 4 }],
  };

  it('round-trips through storage', () => {
    expect(saveHighlights(PAPER, [one])).toBe(true);
    expect(loadHighlights(PAPER)).toEqual([one]);
  });

  it('keeps papers apart', () => {
    saveHighlights(PAPER, [one]);
    expect(loadHighlights('arxiv:9999.99999')).toEqual([]);
  });

  it('clears the key rather than storing an empty list', () => {
    saveHighlights(PAPER, [one]);
    saveHighlights(PAPER, []);
    expect(store.has(`rs-highlights:${PAPER}`)).toBe(false);
    expect(loadHighlights(PAPER)).toEqual([]);
  });

  it('returns nothing for a value that is not highlights', () => {
    store.set(`rs-highlights:${PAPER}`, '{"not":"an array"}');
    expect(loadHighlights(PAPER)).toEqual([]);
  });

  it('returns nothing for a half-written value', () => {
    store.set(`rs-highlights:${PAPER}`, '[{"id":"a",');
    expect(loadHighlights(PAPER)).toEqual([]);
  });

  it('drops entries that are missing what a highlight needs', () => {
    store.set(`rs-highlights:${PAPER}`, JSON.stringify([one, { id: 'b' }]));
    expect(loadHighlights(PAPER)).toEqual([one]);
  });

  it('reports a refused write instead of throwing', () => {
    // Private browsing refuses writes; reading a paper must not break because of it.
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('denied');
      },
      setItem: () => {
        throw new Error('denied');
      },
      removeItem: () => {
        throw new Error('denied');
      },
    });
    expect(saveHighlights(PAPER, [one])).toBe(false);
    expect(loadHighlights(PAPER)).toEqual([]);
  });
});

describe('highlightAt', () => {
  const items: Highlight[] = [
    { id: 'under', page: 1, color: 'yellow', text: 'a', rects: [{ x: 0, y: 0, w: 50, h: 10 }] },
    { id: 'over', page: 1, color: 'green', text: 'b', rects: [{ x: 20, y: 0, w: 50, h: 10 }] },
    { id: 'other', page: 2, color: 'pink', text: 'c', rects: [{ x: 0, y: 0, w: 50, h: 10 }] },
  ];

  it('finds the highlight under a point', () => {
    expect(highlightAt(items, 1, 10, 5, 1)?.id).toBe('under');
  });

  it('prefers the newest where two overlap', () => {
    expect(highlightAt(items, 1, 30, 5, 1)?.id).toBe('over');
  });

  it('respects the scale the page is drawn at', () => {
    // x=80 is past every highlight at 1x, and inside the second one at 2x.
    expect(highlightAt(items, 1, 80, 5, 1)).toBeNull();
    expect(highlightAt(items, 1, 80, 5, 2)?.id).toBe('over');
    // Vertically too: a 10-tall band reaches y=15 only once it is drawn at 2x.
    expect(highlightAt(items, 1, 10, 15, 1)).toBeNull();
    expect(highlightAt(items, 1, 10, 15, 2)?.id).toBe('under');
  });

  it('does not reach onto another page', () => {
    expect(highlightAt(items, 3, 10, 5, 1)).toBeNull();
  });

  it('returns nothing where there is nothing', () => {
    expect(highlightAt(items, 1, 400, 400, 1)).toBeNull();
  });
});

describe('newId', () => {
  it('does not repeat within a paper', () => {
    const ids = new Set([newId(1000), newId(1000), newId(1000)]);
    expect(ids.size).toBe(3);
  });
});
