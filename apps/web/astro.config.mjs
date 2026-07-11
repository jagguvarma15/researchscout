// @ts-check
import { defineConfig } from 'astro/config';

import node from '@astrojs/node';

import vercel from '@astrojs/vercel';

import svelte from '@astrojs/svelte';

import icon from 'astro-icon';

// SSR everywhere: the feed reflects the live database, so nothing is prerendered.
// Vercel sets VERCEL=1 at build time; everywhere else (compose, k3d, the VM) the
// standalone Node server keeps working unchanged. maxDuration covers the chat
// proxy, which holds the function open while the model streams tokens.
export default defineConfig({
  output: 'server',

  adapter: process.env.VERCEL
    ? vercel({ maxDuration: 300 })
    : node({ mode: 'standalone' }),

  integrations: [svelte(), icon()],
});
