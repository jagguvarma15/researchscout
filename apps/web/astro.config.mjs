// @ts-check
import { defineConfig } from 'astro/config';

import node from '@astrojs/node';

import svelte from '@astrojs/svelte';

import icon from 'astro-icon';

import {
  CSP_DIRECTIVES,
  CSP_SCRIPT_RESOURCES,
  CSP_STYLE_RESOURCES,
  THEME_SCRIPT_HASH,
} from './src/lib/csp.js';

// SSR everywhere: the feed reflects the live database, so nothing is prerendered.
// The standalone Node server runs the app directly on the host.
export default defineConfig({
  output: 'server',

  adapter: node({ mode: 'standalone' }),

  integrations: [svelte(), icon()],

  security: {
    // Astro hashes the inline scripts it emits for island hydration, which is the part that
    // cannot be maintained by hand: those hashes change whenever component code does.
    csp: {
      directives: CSP_DIRECTIVES,
      scriptDirective: { resources: CSP_SCRIPT_RESOURCES, hashes: [THEME_SCRIPT_HASH] },
      styleDirective: { resources: CSP_STYLE_RESOURCES },
    },
  },
});
