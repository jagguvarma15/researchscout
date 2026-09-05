import { describe, expect, it } from 'vitest';

import { heatFraction, heatRange, type HeatRow } from './heat';

describe('heatRange', () => {
  it('records the measured min and max of each column any row has scored', () => {
    const rows: HeatRow[] = [{ scores: { a: 0.2, b: 0.5 } }, { scores: { a: 0.8 } }];
    const columns = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    const ranges = heatRange(rows, columns);
    expect(ranges.get('a')).toEqual({ min: 0.2, max: 0.8 });
    expect(ranges.get('b')).toEqual({ min: 0.5, max: 0.5 });
    // No row scored column c, so it has no range.
    expect(ranges.has('c')).toBe(false);
  });
});

describe('heatFraction', () => {
  const ranges = heatRange([{ scores: { a: 0.2 } }, { scores: { a: 0.8 } }], [{ id: 'a' }]);

  it('places a value in its column spread, 0..1', () => {
    expect(heatFraction(ranges, 'a', 0.5)).toBeCloseTo(0.5);
    expect(heatFraction(ranges, 'a', 0.2)).toBe(0);
    expect(heatFraction(ranges, 'a', 0.8)).toBe(1);
  });

  it('returns 1 for a column with no spread, or one with no range at all', () => {
    const flat = heatRange([{ scores: { a: 0.4 } }], [{ id: 'a' }]);
    expect(heatFraction(flat, 'a', 0.4)).toBe(1);
    expect(heatFraction(ranges, 'missing', 0.5)).toBe(1);
  });
});
