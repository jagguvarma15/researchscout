// @ts-check
import { defineConfig } from 'astro/config';

import node from '@astrojs/node';

import svelte from '@astrojs/svelte';

import icon from 'astro-icon';

// SSR everywhere: the feed reflects the live database, so nothing is prerendered.
export default defineConfig({
  output: 'server',

  adapter: node({
    mode: 'standalone',
  }),

  integrations: [svelte(), icon()],
});
