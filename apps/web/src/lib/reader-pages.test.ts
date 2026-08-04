// The reader keeps only the pages near the viewport on a canvas, so these pin the edges of
// that decision: the first page, the last page, and the handover in between. Getting the
// range wrong shows a blank where a page should be, which is the one failure a reader
// notices immediately.

import { describe, expect, it } from 'vitest';

import {
  currentPage,
  layoutPages,
  scrollTopFor,
  totalHeight,
  visibleRange,
} from './reader-pages';

// Four pages, 100 tall, 10 apart: tops at 0, 110, 220, 330.
const HEIGHTS = [100, 100, 100, 100];
const GAP = 10;
const boxes = layoutPages(HEIGHTS, GAP);

describe('layoutPages', () => {
  it('stacks pages with the gap between them', () => {
    expect(boxes).toEqual([
      { top: 0, height: 100 },
      { top: 110, height: 100 },
      { top: 220, height: 100 },
      { top: 330, height: 100 },
    ]);
  });

  it('handles pages of differing heights', () => {
    expect(layoutPages([50, 120], 10)).toEqual([
      { top: 0, height: 50 },
      { top: 60, height: 120 },
    ]);
  });

  it('lays out nothing for no pages', () => {
    expect(layoutPages([], 10)).toEqual([]);
  });
});

describe('totalHeight', () => {
  it('stops at the bottom of the last page, with no trailing gap', () => {
    expect(totalHeight(boxes)).toBe(430);
  });

  it('is zero before the document has loaded', () => {
    expect(totalHeight([])).toBe(0);
  });
});

describe('visibleRange', () => {
  it('covers the pages touching the viewport plus the overscan', () => {
    // Viewport 0-150 touches pages 0 and 1; overscan 1 adds page 2.
    expect(visibleRange(boxes, 0, 150, 1)).toEqual({ first: 0, last: 2 });
  });

  it('does not run off the start or the end', () => {
    expect(visibleRange(boxes, 0, 50, 2).first).toBe(0);
    expect(visibleRange(boxes, 400, 50, 2).last).toBe(3);
  });

  it('counts a page that is only partly on screen', () => {
    // Viewport 90-190: page 0 has ten pixels showing, page 1 most of itself.
    expect(visibleRange(boxes, 90, 100, 0)).toEqual({ first: 0, last: 1 });
  });

  it('excludes a page that has only its gap on screen', () => {
    // Viewport 100-108 sits entirely in the gap below page 0; the nearest page is kept.
    expect(visibleRange(boxes, 100, 8, 0)).toEqual({ first: 3, last: 3 });
  });

  it('keeps the first page when scrolled above the document', () => {
    expect(visibleRange(boxes, -200, 50, 0)).toEqual({ first: 0, last: 0 });
  });

  it('reports nothing to render for an empty document', () => {
    expect(visibleRange([], 0, 500, 1)).toEqual({ first: 0, last: -1 });
  });
});

describe('currentPage', () => {
  it('is the first page at the top', () => {
    expect(currentPage(boxes, 0, 200)).toBe(1);
  });

  it('follows whichever page is showing most of itself', () => {
    // Viewport 60-160: page 0 shows 40, page 1 shows 50.
    expect(currentPage(boxes, 60, 100)).toBe(2);
    // Viewport 40-140: page 0 shows 60, page 1 shows 30.
    expect(currentPage(boxes, 40, 100)).toBe(1);
  });

  it('gives ties to the earlier page, so the number does not flicker', () => {
    // Viewport 50-160: page 0 shows 50, page 1 shows 50.
    expect(currentPage(boxes, 50, 110)).toBe(1);
  });

  it('reaches the last page at the bottom', () => {
    expect(currentPage(boxes, 330, 100)).toBe(4);
  });

  it('is page one for an empty document', () => {
    expect(currentPage([], 0, 500)).toBe(1);
  });
});

describe('scrollTopFor', () => {
  it('lands on the top of the requested page', () => {
    expect(scrollTopFor(boxes, 3)).toBe(220);
  });

  it('clamps a page number outside the document', () => {
    expect(scrollTopFor(boxes, 0)).toBe(0);
    expect(scrollTopFor(boxes, 99)).toBe(330);
  });

  it('is the top for an empty document', () => {
    expect(scrollTopFor([], 4)).toBe(0);
  });
});
