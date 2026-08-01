import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';

// The tested modules are DOM-free by design (see src/lib), so no jsdom. The svelte
// plugin compiles runes in *.svelte.test.ts files (the state-semantics regression test).
export default defineConfig({
  plugins: [svelte()],
  // Client-side svelte in tests: the browser condition resolves the client runtime, and
  // the jsdom environment gives vitest web-mode transforms - under node/ssr transforms
  // the runes compile to their server no-ops and $effect never runs.
  resolve: {
    conditions: ['browser'],
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
  },
});
