import { describe, expect, it } from 'vitest';

import { defaultWindow, lookbackWindows, parseWindow } from './windows';

describe('lookbackWindows', () => {
  it('offers the stock windows when retention is off', () => {
    expect(lookbackWindows(null)).toEqual([7, 30, 90]);
    expect(lookbackWindows(undefined)).toEqual([7, 30, 90]);
    expect(lookbackWindows(0)).toEqual([7, 30, 90]);
  });

  it('fits every window inside the retention period', () => {
    expect(lookbackWindows(10)).toEqual([3, 7, 10]);
    expect(lookbackWindows(7)).toEqual([3, 7]);
    expect(lookbackWindows(5)).toEqual([3, 5]);
    expect(lookbackWindows(2)).toEqual([2]);
  });
});

describe('defaultWindow', () => {
  it('is the retention period when set, else thirty days', () => {
    expect(defaultWindow(10)).toBe(10);
    expect(defaultWindow(null)).toBe(30);
    expect(defaultWindow(0)).toBe(30);
  });
});

describe('parseWindow', () => {
  it('accepts whole days the API accepts', () => {
    expect(parseWindow('7')).toBe(7);
    expect(parseWindow('365')).toBe(365);
  });

  it('rejects everything else as the default', () => {
    expect(parseWindow(null)).toBeNull();
    expect(parseWindow('')).toBeNull();
    expect(parseWindow('0')).toBeNull();
    expect(parseWindow('366')).toBeNull();
    expect(parseWindow('7.5')).toBeNull();
    expect(parseWindow('-3')).toBeNull();
    expect(parseWindow('abc')).toBeNull();
  });
});
