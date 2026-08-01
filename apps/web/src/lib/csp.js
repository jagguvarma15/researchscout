// The content security policy.
//
// Astro generates it (astro.config.mjs reads what is here) because the hashes for its island
// hydration scripts change whenever component code does, and hand-maintained hashes would
// silently break hydration on the next build. What is set here is everything Astro does not
// infer: the sources, and the hash for the one inline script this app writes itself.
//
// style-src deliberately allows inline. pdf.js positions every glyph of its text layer with a
// style attribute, and hashes cannot cover attributes - a policy that breaks the reader is
// worse than one that admits inline CSS. The exfiltration route that usually argues against
// it, a url() pointing off-site, is closed by img-src and connect-src below.
//
// Plain JavaScript, not TypeScript: astro.config.mjs imports it.

import { createHash } from 'node:crypto';

import { THEME_SCRIPT } from './theme-script.js';

/** The hash of the one inline script on the site, computed from the source the layout inlines. */
export const THEME_SCRIPT_HASH = `sha256-${createHash('sha256').update(THEME_SCRIPT).digest('base64')}`;

/** Everything except script-src and style-src, which Astro builds from the directives below. */
export const CSP_DIRECTIVES = [
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
  // The API is same-origin through the proxy; arXiv is where the reader fetches PDFs from.
  "connect-src 'self' https://arxiv.org https://export.arxiv.org",
  // Some stored arXiv links are http; this upgrades them rather than letting a browser block
  // them as mixed content on the deployed site.
  'upgrade-insecure-requests',
];

/** Sources for script-src, alongside the hashes Astro computes for its own inline scripts. */
export const CSP_SCRIPT_RESOURCES = ["'self'", 'blob:'];

/** Sources for style-src. See the note above about inline styles. */
export const CSP_STYLE_RESOURCES = ["'self'", "'unsafe-inline'"];
