import { afterEach, expect, it, vi } from 'vitest';

import { prefersReducedMotion } from './motion';

afterEach(() => {
  document.documentElement.removeAttribute('data-motion');
  vi.unstubAllGlobals();
});

function stubMedia(matches: boolean): void {
  vi.stubGlobal('matchMedia', (query: string) => ({ query, matches }));
}

it('is quiet by default', () => {
  stubMedia(false);
  expect(prefersReducedMotion()).toBe(false);
});

it('honors the site setting without any OS signal', () => {
  stubMedia(false);
  document.documentElement.dataset.motion = 'reduced';
  expect(prefersReducedMotion()).toBe(true);
});

it('honors the OS switch without the site setting', () => {
  stubMedia(true);
  expect(prefersReducedMotion()).toBe(true);
});

it('survives a DOM without matchMedia', () => {
  vi.stubGlobal('matchMedia', undefined);
  expect(prefersReducedMotion()).toBe(false);
});
