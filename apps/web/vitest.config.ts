import { defineConfig } from 'vitest/config';

// The tested modules are DOM-free by design (see src/lib), so no jsdom.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
