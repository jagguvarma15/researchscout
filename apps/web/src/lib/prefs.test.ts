import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  applyTheme,
  parseFeedDefaults,
  readPrefs,
  serializeFeedDefaults,
  themeChoice,
  updatePrefs,
} from './prefs';

// A storage of our own rather than the environment's - the same dodge chat-persist.test.ts
// documents: Node 22 defines a `localStorage` global that throws unless the process was
// started with --localstorage-file, and it shadows the one jsdom provides.
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
  for (const name of ['data-accent', 'data-fontsize', 'data-density', 'data-motion']) {
    document.documentElement.removeAttribute(name);
  }
});

describe('readPrefs', () => {
  it('accepts only whitelisted values from a current envelope', () => {
    localStorage.setItem(
      'rs-prefs',
      JSON.stringify({ v: 1, accent: 'ocean', fontSize: 'huge', density: 'compact', extra: 1 })
    );
    expect(readPrefs()).toEqual({ accent: 'ocean', density: 'compact' });
  });

  it('drops an envelope from another version', () => {
    localStorage.setItem('rs-prefs', JSON.stringify({ v: 2, accent: 'ocean' }));
    expect(readPrefs()).toEqual({});
  });

  it.each(['not json', '"a string"', '[]', 'null'])('survives %s', (raw) => {
    localStorage.setItem('rs-prefs', raw);
    expect(readPrefs()).toEqual({});
  });
});

describe('updatePrefs', () => {
  it('sets, keeps and clears independently', () => {
    updatePrefs({ accent: 'plum', fontSize: 'large' });
    expect(readPrefs()).toEqual({ accent: 'plum', fontSize: 'large' });
    expect(document.documentElement.getAttribute('data-accent')).toBe('plum');
    expect(document.documentElement.getAttribute('data-fontsize')).toBe('large');

    updatePrefs({ accent: null });
    expect(readPrefs()).toEqual({ fontSize: 'large' });
    expect(document.documentElement.hasAttribute('data-accent')).toBe(false);
    expect(document.documentElement.getAttribute('data-fontsize')).toBe('large');
  });

  it('removes the stored envelope when nothing is left', () => {
    updatePrefs({ motion: 'reduced' });
    updatePrefs({ motion: null });
    expect(localStorage.getItem('rs-prefs')).toBeNull();
  });
});

describe('theme', () => {
  it('reads absence as system', () => {
    expect(themeChoice()).toBe('system');
    localStorage.setItem('rs-theme', 'dark');
    expect(themeChoice()).toBe('dark');
    localStorage.setItem('rs-theme', 'nonsense');
    expect(themeChoice()).toBe('system');
  });

  it('applies an explicit choice to storage, the page and the chrome color', () => {
    document.head.insertAdjacentHTML('beforeend', '<meta name="theme-color" content="#fff">');
    const resolved = applyTheme('dark');
    expect(resolved).toBe('dark');
    expect(localStorage.getItem('rs-theme')).toBe('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(
      document.querySelector('meta[name="theme-color"]')?.getAttribute('content')
    ).toBe('#191713');
  });

  it('announces changes for other islands', () => {
    let seen: unknown = null;
    document.addEventListener('rs:themechange', (event) => {
      seen = (event as CustomEvent).detail;
    });
    applyTheme('light');
    expect(seen).toEqual({ choice: 'light', resolved: 'light' });
  });
});

describe('parseFeedDefaults', () => {
  it('accepts raw and URI-encoded JSON alike', () => {
    const defaults = { sort: 'citations', days: '30', topic: 'nlp' } as const;
    expect(parseFeedDefaults(JSON.stringify(defaults))).toEqual(defaults);
    expect(parseFeedDefaults(serializeFeedDefaults(defaults))).toEqual(defaults);
  });

  it('keeps valid fields and drops the rest', () => {
    expect(parseFeedDefaults(JSON.stringify({ sort: 'newest', days: 90, topic: 'x' }))).toEqual({
      sort: 'newest',
    });
  });

  it.each([null, undefined, '', 'garbage', '%ZZ', '[]', '"str"', '{}'])(
    'returns null for %s',
    (raw) => {
      expect(parseFeedDefaults(raw)).toBeNull();
    }
  );
});
