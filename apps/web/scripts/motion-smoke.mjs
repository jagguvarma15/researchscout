// Probe the animation wave's STATE guarantees against the built site - stills cannot show
// motion, but every animation here leaves a testable trace: an attribute with a lifetime,
// a gated API call, a class that must toggle, a row that must still leave. Run after
// `pnpm build`, with API_URL/API_SERVICE_TOKEN pointing at a live backend (the feed probes
// need rows and a pager):
//   node scripts/motion-smoke.mjs
// Exits 1 on any FAIL. Companion to visual-smoke.mjs, which owns the pictures.

import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = fileURLToPath(new URL('..', import.meta.url));
const PORT = 4396;
const BASE = `http://127.0.0.1:${PORT}`;

const results = [];
function record(name, ok, note = '') {
  results.push([name, ok, note]);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${note ? ` - ${note}` : ''}`);
}

// A stale server squatting the port would silently probe OLD code (it produced a false
// alarm in the previous wave); refuse to start over one.
try {
  await fetch(BASE, { redirect: 'manual' });
  console.error(`motion-smoke: something already answers on ${BASE} - kill it first (lsof -ti :${PORT})`);
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

// Counts our own startViewTransition calls; the router's navigations also go through the
// wrapped function, so probes that use the counter must not navigate.
const COUNTER = () => {
  const original = document.startViewTransition?.bind(document);
  window.__vtCount = 0;
  if (original) {
    document.startViewTransition = (callback) => {
      window.__vtCount += 1;
      return original(callback);
    };
  }
};

async function probePagingLifecycle(browser) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForTimeout(1000);
  if (!(await page.$('[data-page-dir="older"]'))) {
    record('paging lifecycle', false, 'no pager - is the API up with enough papers?');
    await context.close();
    return;
  }
  const seen = page
    .waitForFunction(() => document.documentElement.dataset.paging === 'older', { timeout: 3000 })
    .then(() => true)
    .catch(() => false);
  await page.click('[data-page-dir="older"]');
  const appeared = await seen;
  const cleared = await page
    .waitForFunction(() => !('paging' in document.documentElement.dataset), { timeout: 5000 })
    .then(() => true)
    .catch(() => false);
  record(
    'paging lifecycle',
    appeared && cleared,
    appeared ? (cleared ? 'set during, cleared after' : 'attribute never cleared') : 'attribute never set',
  );
  await context.close();
}

async function probeThemeGate(browser) {
  // Unrestricted: a theme click must run exactly one view transition, and the motion
  // setter none at all.
  let context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await context.addInitScript(COUNTER);
  let page = await context.newPage();
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForTimeout(800);
  await page.click('[data-open-settings]');
  await page.waitForTimeout(400);
  await page.click('.prefs-panel button:text-is("Dark")');
  await page.waitForTimeout(500);
  const afterDark = await page.evaluate(() => ({
    count: window.__vtCount,
    theme: document.documentElement.dataset.theme,
  }));
  await page.click('.prefs-panel button:text-is("Reduced")');
  await page.waitForTimeout(500);
  const afterReduced = await page.evaluate(() => window.__vtCount);
  record(
    'theme uses one view transition',
    afterDark.count === 1 && afterDark.theme === 'dark',
    `count ${afterDark.count}, theme ${afterDark.theme}`,
  );
  record('engaging reduced motion never animates', afterReduced === afterDark.count, `count ${afterReduced}`);
  await context.close();

  // Seeded reduced: the same click must flip the theme without any transition.
  context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await context.addInitScript(COUNTER);
  await context.addInitScript(() => {
    localStorage.setItem('rs-prefs', JSON.stringify({ v: 1, motion: 'reduced' }));
  });
  page = await context.newPage();
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForTimeout(800);
  await page.click('[data-open-settings]');
  await page.waitForTimeout(400);
  await page.click('.prefs-panel button:text-is("Dark")');
  await page.waitForTimeout(500);
  const gated = await page.evaluate(() => ({
    count: window.__vtCount,
    theme: document.documentElement.dataset.theme,
  }));
  record(
    'site switch gates the transition',
    gated.count === 0 && gated.theme === 'dark',
    `count ${gated.count}, theme ${gated.theme}`,
  );
  await context.close();
}

async function probeStuckHeadings(browser) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForTimeout(1000);
  if (!(await page.$('.day .day-heading'))) {
    record('pinned-date shadow', false, 'no day headings - is the API up?');
    await context.close();
    return;
  }
  const check = async () => {
    await page.waitForTimeout(400);
    return page.evaluate(() => Boolean(document.querySelector('.day .day-heading.stuck')));
  };
  await page.evaluate(() => window.scrollTo(0, 900));
  const pinned = await check();
  await page.evaluate(() => window.scrollTo(0, 0));
  const unpinned = !(await check());
  // The observer must survive a soft navigation away and back (module scripts run once).
  await page.click('.rail a[href="/about"]');
  await page.waitForTimeout(900);
  await page.click('.brand');
  await page.waitForTimeout(1200);
  await page.evaluate(() => window.scrollTo(0, 900));
  const rebound = await check();
  record(
    'pinned-date shadow',
    pinned && unpinned && rebound,
    `pinned ${pinned}, cleared ${unpinned}, after soft-nav ${rebound}`,
  );
  await context.close();
}

async function probeRevealAndDismiss(browser) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForTimeout(1000);
  if (!(await page.$('.feed .row'))) {
    record('reveal settles and dismiss detaches', false, 'no feed rows - is the API up?');
    await context.close();
    return;
  }
  await page.evaluate(() => window.scrollTo(0, 500));
  await page.waitForTimeout(600);
  const opaque = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll('.feed .row'));
    const inView = rows.filter((row) => {
      const box = row.getBoundingClientRect();
      return box.bottom > 0 && box.top < window.innerHeight;
    });
    return inView.length > 0 && inView.every((row) => getComputedStyle(row).opacity === '1');
  });
  const before = await page.evaluate(() => document.querySelectorAll('.feed .row').length);
  await page.click('.feed .row [data-dismiss]');
  const detached = await page
    .waitForFunction(
      (count) => document.querySelectorAll('.feed .row').length < count,
      before,
      { timeout: 1500 },
    )
    .then(() => true)
    .catch(() => false);
  record(
    'reveal settles and dismiss detaches',
    opaque && detached,
    `in-view opacity 1: ${opaque}, row left within 1.5s: ${detached}`,
  );
  await context.close();
}

async function probeSegIntegrity(browser) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForTimeout(800);
  await page.click('[data-open-settings]');
  await page.waitForTimeout(400);
  const count = async () =>
    page.evaluate(() => {
      const named = Array.from(document.querySelectorAll('[style*="seg-theme"]'));
      return { total: named.length, on: named.filter((el) => el.classList.contains('on')).length };
    });
  const beforeClick = await count();
  await page.click('.prefs-panel button:text-is("Dark")');
  await page.waitForTimeout(600);
  const afterClick = await count();
  const ok =
    beforeClick.total === 1 && beforeClick.on === 1 && afterClick.total === 1 && afterClick.on === 1;
  record(
    'one seg-theme name, always on the active pill',
    ok,
    `before ${beforeClick.total}/${beforeClick.on}, after ${afterClick.total}/${afterClick.on}`,
  );
  await context.close();
}

try {
  await waitForServer();
  const browser = await chromium.launch();
  await probePagingLifecycle(browser);
  await probeThemeGate(browser);
  await probeStuckHeadings(browser);
  await probeRevealAndDismiss(browser);
  await probeSegIntegrity(browser);
  await browser.close();
} finally {
  server.kill();
}

const failed = results.filter(([, ok]) => !ok);
console.log(`motion-smoke: ${results.length - failed.length}/${results.length} probes green`);
process.exitCode = failed.length > 0 ? 1 : 0;
