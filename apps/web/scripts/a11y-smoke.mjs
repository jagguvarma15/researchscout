// Accessibility smoke: axe-core over the built site's representative pages at both widths.
// The unit suite covers pure logic and verify-styles covers CSS presence; nothing else
// checks the accessibility tree, and this wave touches the markup of nearly every page.
// Run after `pnpm build`, with API_URL pointing at a live backend so the pages carry their
// real content:
//   node scripts/a11y-smoke.mjs
// Exits 1 on any violation. Manual like the other smokes - not wired into CI, because the
// interesting pages need a data-populated API. The manifest colors are launch chrome, not
// page content, so nothing here checks them.

import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = fileURLToPath(new URL('..', import.meta.url));
const PORT = 4395;
const BASE = `http://127.0.0.1:${PORT}`;
const AXE_SOURCE = readFileSync(
  new URL('../node_modules/axe-core/axe.min.js', import.meta.url),
  'utf8'
);

// One page per archetype: the feed, the simple lists, both catalogue shapes, the trend
// cards, the issue archive, the profile column, and the prose page with the live table.
// The detail archetypes (paper article, digest issue, topic page) join at runtime with a
// live id each - see discoverDetails.
const PAGES = ['/', '/saved', '/for-you', '/topics', '/digests', '/models', '/benchmarks', '/profile', '/about', '/search?q=attention', '/trends'];
const WIDTHS = [
  { name: 'phone', viewport: { width: 390, height: 844 } },
  { name: 'desktop', viewport: { width: 1280, height: 800 } },
];

const results = [];
function record(name, ok, note = '') {
  results.push([name, ok, note]);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${note ? ` - ${note}` : ''}`);
}

// A stale server squatting the port would silently probe OLD code; refuse to start over one.
try {
  await fetch(BASE, { redirect: 'manual' });
  console.error(
    `a11y-smoke: something already answers on ${BASE} - kill it first (lsof -ti :${PORT})`
  );
  process.exit(1);
} catch {
  // nothing listening - good
}

const server = spawn('node', ['dist/server/entry.mjs'], {
  cwd: root,
  env: {
    ...process.env,
    HOST: '127.0.0.1',
    PORT: String(PORT),
    API_URL: process.env.API_URL ?? 'http://127.0.0.1:8000',
  },
  stdio: 'ignore',
});

async function waitForServer() {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(BASE, { redirect: 'manual' });
      if (response.status < 500) return;
    } catch {
      // not up yet
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error('the built server did not start; did you run pnpm build?');
}

async function auditPage(browser, viewportName, viewport, path) {
  // bypassCSP because the site ships a hashed script-src that (correctly) refuses the
  // injected axe source; the audit reads the DOM, so the policy changes nothing it sees.
  const context = await browser.newContext({ viewport, bypassCSP: true });
  const page = await context.newPage();
  try {
    await page.goto(`${BASE}${path}`, { waitUntil: 'load' });
    // Let the islands hydrate; axe reads the settled tree.
    await page.waitForTimeout(800);
    // Walk to the bottom and audit from there: the feed's scroll-driven reveal holds rows
    // below the fold at reduced opacity, and the timeline is bidirectional, so scrolling
    // back up would re-arm it - the settled state for the whole document is at the end.
    // The walk also hydrates the client:visible islands on the way down.
    await page.evaluate(async () => {
      const step = window.innerHeight;
      for (let y = 0; y <= document.body.scrollHeight; y += step) {
        window.scrollTo(0, y);
        await new Promise((resolve) => setTimeout(resolve, 60));
      }
    });
    await page.waitForTimeout(300);
    await page.addScriptTag({ content: AXE_SOURCE });
    const violations = await page.evaluate(async () => {
      const outcome = await window.axe.run(document, { resultTypes: ['violations'] });
      return outcome.violations.map((violation) => ({
        id: violation.id,
        impact: violation.impact,
        help: violation.help,
        nodes: violation.nodes.length,
        firstTarget: violation.nodes[0]?.target?.join(' ') ?? '',
      }));
    });
    const label = `${path} at ${viewportName}`;
    if (violations.length === 0) {
      record(label, true);
    } else {
      record(label, false, `${violations.length} rule${violations.length === 1 ? '' : 's'}`);
      for (const violation of violations) {
        console.log(
          `      ${violation.id} (${violation.impact}): ${violation.help} - ` +
            `${violation.nodes} node${violation.nodes === 1 ? '' : 's'}, first: ${violation.firstTarget}`
        );
      }
    }
  } catch (error) {
    // A page that cannot even be audited is a failure of the run, not a crash of it.
    record(`${path} at ${viewportName}`, false, String(error).split('\n')[0]);
  } finally {
    await context.close();
  }
}

// One live id per detail archetype, read through the site's own proxy; with the API down
// the audit covers the static routes and says what it skipped.
async function discoverDetails() {
  const probes = [
    ['/api/papers?limit=1', (body) => body.items?.[0]?.id, (id) => `/papers/${id}`],
    ['/api/digests', (body) => body.items?.[0]?.slug, (slug) => `/digests/${slug}`],
    ['/api/topics', (body) => body.items?.[0]?.id, (id) => `/topics/${id}`],
  ];
  const paths = [];
  for (const [probe, pick, toPath] of probes) {
    try {
      const response = await fetch(`${BASE}${probe}`);
      if (!response.ok) continue;
      const key = pick(await response.json());
      if (key !== undefined && key !== null) paths.push(toPath(key));
    } catch {
      // API down
    }
  }
  return paths;
}

let browser;
try {
  await waitForServer();
  const details = await discoverDetails();
  if (details.length < 3) {
    console.log(`note: only ${details.length}/3 detail pages discovered - is the API up?`);
  }
  browser = await chromium.launch();
  for (const { name, viewport } of WIDTHS) {
    for (const path of [...PAGES, ...details]) {
      await auditPage(browser, name, viewport, path);
    }
  }
} finally {
  await browser?.close();
  server.kill();
}

const failed = results.filter(([, ok]) => !ok);
console.log(`\na11y-smoke: ${results.length - failed.length}/${results.length} clean`);
process.exit(failed.length > 0 ? 1 : 0);
