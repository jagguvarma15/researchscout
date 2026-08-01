// The hash of the one inline script this app writes.
//
// The rest of the content security policy lives in astro.config.mjs, because Astro generates
// it: only the build knows the hashes of the island hydration scripts, and those change
// whenever component code does. What cannot be generated is the theme setter, which is
// deliberately inline so the theme is right before first paint - Astro leaves `is:inline`
// scripts alone. Hashing the same constant the layout inlines is what keeps the policy and
// the page from drifting apart.
//
// Plain JavaScript, not TypeScript: astro.config.mjs imports it.

import { createHash } from 'node:crypto';

import { THEME_SCRIPT } from './theme-script.js';

/** @type {`sha256-${string}`} */
export const THEME_SCRIPT_HASH = `sha256-${createHash('sha256').update(THEME_SCRIPT).digest('base64')}`;
