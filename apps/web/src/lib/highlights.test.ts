// A highlight is a box that snaps onto whatever is inside it. These pin the two properties
// that make that work - that a rectangle is stored independently of the zoom it was drawn at,
// and that the snap finds the ink and nothing else - plus the storage failures a reader has to
// survive rather than crash on.

import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  clampRect,
  highlightAt,
  inkBounds,
  loadHighlights,
  newId,
  rectFromDrag,
  saveHighlights,
  sweepHighlights,
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
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() {
      return store.size;
    },
  };
}

beforeEach(() => {
  store = new Map();
  vi.stubGlobal('localStorage', memoryStorage());
});

/** Builds RGBA pixels from rows of characters: '#' is ink, '.' is paper, ' ' is transparent. */
function pixels(rows: string[]): { data: Uint8ClampedArray; width: number; height: number } {
  const width = rows[0].length;
  const height = rows.length;
  const data = new Uint8ClampedArray(width * height * 4);
  rows.forEach((row, y) => {
    [...row].forEach((cell, x) => {
      const i = (y * width + x) * 4;
      const value = cell === '#' ? 0 : 255;
      data[i] = value;
      data[i + 1] = value;
      data[i + 2] = value;
      data[i + 3] = cell === ' ' ? 0 : 255;
    });
  });
  return { data, width, height };
}

describe('toScreenRects', () => {
  it('survives a change of zoom', () => {
    const stored = [{ x: 25, y: 25, w: 30, h: 6 }];
    // Drawn at 2x, painted at 3x: the mark lands where the same content now is.
    expect(toScreenRects(stored, 3)).toEqual([{ left: 75, top: 75, width: 90, height: 18 }]);
    expect(toScreenRects(stored, 2)).toEqual([{ left: 50, top: 50, width: 60, height: 12 }]);
  });
});

describe('rectFromDrag', () => {
  it('reads a drag down and to the right', () => {
    expect(rectFromDrag({ x: 10, y: 20 }, { x: 40, y: 60 })).toEqual({ x: 10, y: 20, w: 30, h: 40 });
  });

  it('reads a drag up and to the left the same way', () => {
    // Dragging back towards the start is as natural as dragging forwards, and a rectangle
    // with negative width would paint nothing and hit-test as empty.
    expect(rectFromDrag({ x: 40, y: 60 }, { x: 10, y: 20 })).toEqual({ x: 10, y: 20, w: 30, h: 40 });
  });

  it('gives a zero rectangle for a click that never moved', () => {
    expect(rectFromDrag({ x: 5, y: 5 }, { x: 5, y: 5 })).toEqual({ x: 5, y: 5, w: 0, h: 0 });
  });
});

describe('clampRect', () => {
  it('leaves a rectangle inside the page alone', () => {
    expect(clampRect({ x: 10, y: 10, w: 20, h: 20 }, 100, 100)).toEqual({
      x: 10,
      y: 10,
      w: 20,
      h: 20,
    });
  });

  it('trims a rectangle that runs off the edge', () => {
    expect(clampRect({ x: 90, y: 90, w: 40, h: 40 }, 100, 100)).toEqual({
      x: 90,
      y: 90,
      w: 10,
      h: 10,
    });
  });

  it('pulls a rectangle that starts off the page back on', () => {
    expect(clampRect({ x: -10, y: -5, w: 30, h: 30 }, 100, 100)).toEqual({
      x: 0,
      y: 0,
      w: 30,
      h: 30,
    });
  });
});

describe('inkBounds', () => {
  it('finds the box around the ink and drops the paper around it', () => {
    const { data, width, height } = pixels([
      '........',
      '..###...',
      '..###...',
      '........',
    ]);
    expect(inkBounds(data, width, height)).toEqual({ x: 2, y: 1, w: 3, h: 2 });
  });

  it('spans everything drawn, however scattered', () => {
    // A displayed equation is ink in several disconnected places; the mark covers all of it.
    const { data, width, height } = pixels([
      '#......#',
      '........',
      '....#...',
    ]);
    expect(inkBounds(data, width, height)).toEqual({ x: 0, y: 0, w: 8, h: 3 });
  });

  it('reports nothing for a patch of blank paper', () => {
    const { data, width, height } = pixels(['....', '....']);
    expect(inkBounds(data, width, height)).toBeNull();
  });

  it('treats transparent pixels as paper', () => {
    const { data, width, height } = pixels(['    ', '    ']);
    expect(inkBounds(data, width, height)).toBeNull();
  });

  it('ignores near-white antialiasing but keeps real marks', () => {
    const { data, width, height } = pixels(['....', '....']);
    // Two pixels just under and just over the threshold.
    data[0] = data[1] = data[2] = 250;
    data[4] = data[5] = data[6] = 200;
    expect(inkBounds(data, width, height)).toEqual({ x: 1, y: 0, w: 1, h: 1 });
  });

  it('takes a threshold, for pages that are not quite white', () => {
    const { data, width, height } = pixels(['..', '..']);
    data[0] = data[1] = data[2] = 250;
    expect(inkBounds(data, width, height, 252)).toEqual({ x: 0, y: 0, w: 1, h: 1 });
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

  it('still reads the pre-envelope bare array and rewrites it on save', () => {
    store.set(`rs-highlights:${PAPER}`, JSON.stringify([one]));
    expect(loadHighlights(PAPER)).toEqual([one]);

    saveHighlights(PAPER, [one]);
    const written = JSON.parse(store.get(`rs-highlights:${PAPER}`) ?? 'null');
    expect(written.v).toBe(1);
    expect(typeof written.savedAt).toBe('number');
    expect(written.items).toEqual([one]);
  });

  it('drops stored items past the field caps', () => {
    store.set(
      `rs-highlights:${PAPER}`,
      JSON.stringify({
        v: 1,
        savedAt: Date.now(),
        items: [
          one,
          { ...one, id: 'b', text: 'x'.repeat(3000) },
          { ...one, id: 'c', page: 0 },
          { ...one, id: 'd', page: 2.5 },
          { ...one, id: 'e', rects: [{ x: 1, y: 2, w: Infinity, h: 4 }] },
        ],
      }),
    );
    const loaded = loadHighlights(PAPER);
    expect(loaded.map((item) => item.id)).toEqual(['a', 'e']);
    // The rect with a non-finite number is dropped; the highlight survives without it.
    expect(loaded[1].rects).toEqual([]);
  });
});

describe('sweep', () => {
  const one: Highlight = {
    id: 'a',
    page: 1,
    color: 'yellow',
    text: 'kept',
    rects: [{ x: 1, y: 2, w: 3, h: 4 }],
  };

  function seedPaper(paperId: string, savedAt: number) {
    store.set(`rs-highlights:${paperId}`, JSON.stringify({ v: 1, savedAt, items: [one] }));
  }

  it('drops papers untouched for half a year and keeps recent ones', () => {
    seedPaper('arxiv:old.1', Date.now() - 181 * 24 * 60 * 60 * 1000);
    seedPaper('arxiv:new.1', Date.now() - 1000);
    sweepHighlights();
    expect(store.has('rs-highlights:arxiv:old.1')).toBe(false);
    expect(store.has('rs-highlights:arxiv:new.1')).toBe(true);
  });

  it('keeps only the newest fifty papers', () => {
    for (let i = 0; i < 55; i += 1) {
      seedPaper(`arxiv:2401.${String(i).padStart(5, '0')}`, Date.now() - i * 1000);
    }
    sweepHighlights();
    const remaining = [...store.keys()].filter((name) => name.startsWith('rs-highlights:'));
    expect(remaining).toHaveLength(50);
    // The five oldest are the ones that went.
    expect(store.has('rs-highlights:arxiv:2401.00054')).toBe(false);
    expect(store.has('rs-highlights:arxiv:2401.00000')).toBe(true);
  });

  it('removes unreadable and empty keys along the way', () => {
    store.set('rs-highlights:arxiv:broken', '[{"id":');
    store.set('rs-highlights:arxiv:empty', JSON.stringify({ v: 1, savedAt: Date.now(), items: [] }));
    store.set('rs-other-key', 'untouched');
    sweepHighlights();
    expect(store.has('rs-highlights:arxiv:broken')).toBe(false);
    expect(store.has('rs-highlights:arxiv:empty')).toBe(false);
    expect(store.has('rs-other-key')).toBe(true);
  });

  it('survives a throwing storage', () => {
    vi.stubGlobal('localStorage', {
      get length(): number {
        throw new Error('denied');
      },
      key: () => null,
      getItem: () => null,
      removeItem: () => undefined,
    });
    expect(() => sweepHighlights()).not.toThrow();
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

describe('notes, merge and markdown', () => {
  const base: Highlight = {
    id: 'a',
    page: 2,
    color: 'amber',
    text: 'the key sentence',
    rects: [{ x: 1, y: 2, w: 30, h: 4 }],
  };

  it('round-trips a note and drops an overlong one', () => {
    saveHighlights(PAPER, [{ ...base, note: 'compare with section 5' }]);
    expect(loadHighlights(PAPER)[0].note).toBe('compare with section 5');

    saveHighlights(PAPER, [{ ...base, note: 'x'.repeat(2001) }]);
    expect(loadHighlights(PAPER)[0].note).toBeUndefined();
  });

  it('merges remote marks without letting them shadow local ones', async () => {
    const { mergeHighlights } = await import('./highlights');
    const local = [{ ...base, note: 'local words' }];
    const remote = [
      { ...base, note: 'older synced words' },
      { ...base, id: 'b', page: 5 },
    ];
    const merged = mergeHighlights(local, remote);
    expect(merged.map((item) => item.id)).toEqual(['a', 'b']);
    expect(merged[0].note).toBe('local words');
  });

  it('renders the marks as a markdown document, page order, notes nested', async () => {
    const { highlightsMarkdown } = await import('./highlights');
    const doc = highlightsMarkdown('Sparse Attention', [
      { ...base, id: 'b', page: 5, text: '', note: 'a figure worth keeping' },
      { ...base, note: 'compare with section 5' },
    ]);
    const lines = doc.split('\n');
    expect(lines[0]).toBe('# Highlights - Sparse Attention');
    expect(lines[2]).toBe('- p2: "the key sentence"');
    expect(lines[3]).toBe('  - compare with section 5');
    expect(lines[4]).toBe('- p5: figure or equation');
    expect(lines[5]).toBe('  - a figure worth keeping');
  });

  it('remembers when sync is unavailable and stops asking', async () => {
    const { pullHighlights, pushHighlights } = await import('./highlights');
    const calls: string[] = [];
    vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url}`);
      return Promise.resolve(new Response(null, { status: 404 })) as Promise<Response>;
    });
    expect(await pullHighlights(PAPER)).toBeNull();
    pushHighlights(PAPER, [base]);
    await Promise.resolve();
    expect(await pullHighlights(PAPER)).toBeNull();
    // The 404 latched after the first call; nothing else went out.
    expect(calls).toHaveLength(1);
  });
});
