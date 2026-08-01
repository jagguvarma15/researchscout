// @ts-check
import { defineConfig } from 'astro/config';

import node from '@astrojs/node';

import svelte from '@astrojs/svelte';

import icon from 'astro-icon';

import { THEME_SCRIPT_HASH } from './src/lib/csp.js';

// SSR everywhere: the feed reflects the live database, so nothing is prerendered.
// The standalone Node server runs the app directly on the host.
export default defineConfig({
  output: 'server',

  adapter: node({ mode: 'standalone' }),

  integrations: [svelte(), icon()],

  security: {
    // Astro emits this policy per response and hashes the inline scripts it renders for island
    // hydration - the part that cannot be maintained by hand, since those hashes change with
    // component code. The theme script is ours, and is admitted by its own hash.
    //
    // style-src allows inline deliberately: pdf.js positions every glyph of its text layer
    // with a style attribute, and hashes cannot cover attributes, so a stricter style-src
    // breaks the reader. Astro appends style hashes anyway, and by the CSP spec a hash makes
    // 'unsafe-inline' inert - src/middleware.ts drops them so this takes effect. The usual
    // argument against inline styles, exfiltration through a url(), is closed by img-src and
    // connect-src below.
    csp: {
      directives: [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        // Clickjacking: no framing at all. X-Frame-Options repeats this for older browsers.
        "frame-ancestors 'none'",
        "form-action 'self'",
        // pdf.js renders pages in a worker it builds as a blob.
        "worker-src 'self' blob:",
        "img-src 'self' data: blob:",
        // Inter ships as woff2 files; KaTeX inlines a couple of fonts as data URIs.
        "font-src 'self' data:",
        // The API is same-origin through the proxy; arXiv is where the reader gets PDFs.
        "connect-src 'self' https://arxiv.org https://export.arxiv.org",
        // Some stored arXiv links are http; upgrade them rather than have a browser block
        // them as mixed content on the deployed site.
        'upgrade-insecure-requests',
      ],
      scriptDirective: { resources: ["'self'", 'blob:'], hashes: [THEME_SCRIPT_HASH] },
      styleDirective: { resources: ["'self'", "'unsafe-inline'"] },
    },
  },
});
