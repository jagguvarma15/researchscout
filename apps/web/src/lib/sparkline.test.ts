import { describe, expect, it } from 'vitest';

import { sparklineGeometry } from './sparkline';

describe('sparklineGeometry', () => {
  it('returns null below two points', () => {
    expect(sparklineGeometry([])).toBeNull();
    expect(sparklineGeometry([5])).toBeNull();
  });

  it('spreads points across the padded width, min to max onto the padded height', () => {
    const geo = sparklineGeometry([3, 14], 100, 28, 4);
    expect(geo).not.toBeNull();
    expect(geo?.line).toBe('4,24 96,4');
    expect(geo?.end).toEqual({ x: 96, y: 4 });
  });

  it('draws a flat series on the midline instead of dividing by zero', () => {
    const geo = sparklineGeometry([6, 6, 6], 100, 28, 4);
    expect(geo?.line).toBe('4,14 50,14 96,14');
  });

  it('closes the area wash to the near-bottom baseline at both ends', () => {
    const geo = sparklineGeometry([3, 14], 100, 28, 4);
    expect(geo?.area).toBe('M4 24 L96 4 L96 26 L4 26 Z');
  });

  it('keeps every coordinate inside the viewBox', () => {
    const geo = sparklineGeometry([9, 2, 30, 7, 30, 1], 640, 120, 8);
    for (const pair of geo!.line.split(' ')) {
      const [x, y] = pair.split(',').map(Number);
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThanOrEqual(640);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y).toBeLessThanOrEqual(120);
    }
  });
});

describe('points and stepLineGeometry', () => {
  it('exposes every reading center in series order', () => {
    const geo = sparklineGeometry([3, 14], 100, 28, 4);
    expect(geo?.points).toEqual([
      { x: 4, y: 24 },
      { x: 96, y: 4 },
    ]);
  });

  it('steps horizontally at the old level before rising', async () => {
    const { stepLineGeometry } = await import('./sparkline');
    const geo = stepLineGeometry([3, 14], 100, 28, 4);
    expect(geo?.path).toBe('M4 24 L96 24 L96 4');
    expect(geo?.end).toEqual({ x: 96, y: 4 });
  });

  it('returns null below two points', async () => {
    const { stepLineGeometry } = await import('./sparkline');
    expect(stepLineGeometry([42])).toBeNull();
  });
});
