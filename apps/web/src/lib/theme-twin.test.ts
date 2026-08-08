import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ACCENTS,
  DENSITIES,
  FONT_SIZES,
  MOTIONS,
  applyTheme,
  readPrefs,
  updatePrefs,
} from './prefs';
import { THEME_SCRIPT } from './theme-script.js';

// theme-script.js must stay a self-contained string so its CSP hash covers everything it
// does, which is why it duplicates prefs.ts's whitelists, storage keys, version gate and
// chrome hexes by hand. Nothing in the type system ties the two - this file does. It runs
// the inline script for real over state the typed module wrote, so any drift in a shared
// fact shows up as a behavioral disagreement rather than a string mismatch.

// The same storage dodge chat-persist.test.ts documents: Node 22 defines a throwing
// `localStorage` global that shadows the one jsdom provides.
let store: Map<string, string>;

function memoryStorage() {
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  };
}

function runInlineScript() {
  new Function(THEME_SCRIPT)();
}

function themeColorMetas(): string[] {
  return [...document.querySelectorAll('meta[name="theme-color"]')].map(
    (meta) => meta.getAttribute('content') ?? ''
  );
}

beforeEach(() => {
  store = new Map();
  vi.stubGlobal('localStorage', memoryStorage());
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn() })
  );
  document.head.innerHTML =
    '<meta name="theme-color" content="#faf7f1" media="(prefers-color-scheme: light)" />' +
    '<meta name="theme-color" content="#191713" media="(prefers-color-scheme: dark)" />';
  for (const name of ['data-theme', 'data-accent', 'data-fontsize', 'data-density', 'data-motion']) {
    document.documentElement.removeAttribute(name);
  }
});

describe('the inline script applies what the typed module stored', () => {
  // Each whitelist member round-trips: updatePrefs proves the typed side accepts it, the
  // script proves the inline side reads the same key, honors the same version envelope,
  // and allows the same value. A member added to one list but not the other fails here.
  it.each(ACCENTS)('accent %s', (accent) => {
    updatePrefs({ accent });
    document.documentElement.removeAttribute('data-accent');
    runInlineScript();
    expect(document.documentElement.getAttribute('data-accent')).toBe(accent);
  });

  it.each(FONT_SIZES)('font size %s', (fontSize) => {
    updatePrefs({ fontSize });
    document.documentElement.removeAttribute('data-fontsize');
    runInlineScript();
    expect(document.documentElement.getAttribute('data-fontsize')).toBe(fontSize);
  });

  it.each(DENSITIES)('density %s', (density) => {
    updatePrefs({ density });
    document.documentElement.removeAttribute('data-density');
    runInlineScript();
    expect(document.documentElement.getAttribute('data-density')).toBe(density);
  });

  it.each(MOTIONS)('motion %s', (motion) => {
    updatePrefs({ motion });
    document.documentElement.removeAttribute('data-motion');
    runInlineScript();
    expect(document.documentElement.getAttribute('data-motion')).toBe(motion);
  });

  it.each(['dark', 'light'] as const)('theme choice %s', (choice) => {
    applyTheme(choice);
    document.documentElement.removeAttribute('data-theme');
    runInlineScript();
    expect(document.documentElement.dataset.theme).toBe(choice);
  });
});

describe('both sides reject the same envelopes', () => {
  it('a value outside the whitelist', () => {
    localStorage.setItem('rs-prefs', JSON.stringify({ v: 1, accent: 'neon' }));
    runInlineScript();
    expect(document.documentElement.hasAttribute('data-accent')).toBe(false);
    expect(readPrefs()).toEqual({});
  });

  it('an envelope from another version', () => {
    localStorage.setItem('rs-prefs', JSON.stringify({ v: 2, accent: ACCENTS[0] }));
    runInlineScript();
    expect(document.documentElement.hasAttribute('data-accent')).toBe(false);
    expect(readPrefs()).toEqual({});
  });
});

describe('both sides write the same chrome color', () => {
  it.each([
    ['dark', '#191713'],
    ['light', '#faf7f1'],
  ] as const)('for %s, into every theme-color meta', (choice, hex) => {
    applyTheme(choice);
    const typed = themeColorMetas();
    for (const meta of document.querySelectorAll('meta[name="theme-color"]')) {
      meta.setAttribute('content', 'junk');
    }
    runInlineScript();
    const inline = themeColorMetas();
    expect(inline).toEqual(typed);
    // Pinned literally as well, so a matching change on both sides still surfaces in review.
    expect(inline).toEqual([hex, hex]);
  });
});
