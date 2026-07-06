// @ts-check
import { defineConfig } from 'astro/config';

import node from '@astrojs/node';

// SSR everywhere: the feed reflects the live database, so nothing is prerendered.
export default defineConfig({
  output: 'server',
  adapter: node({
    mode: 'standalone',
  }),
});
