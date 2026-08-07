// Render the BUILT site and screenshot the surfaces that keep shipping sight-unseen: the
// home page, the open drawer, and the Scout chat panel with a seeded conversation - at
// phone and desktop widths. The point is to look at the pictures before merging; dev mode
// cannot stand in for this, because Vite injects component CSS there and the production
// bug this guards against (missing extracted styles) never shows.
//
// Run after `pnpm build` (and `pnpm exec playwright install chromium` once):
//   node scripts/visual-smoke.mjs
// Output: .smoke/*.png (gitignored). The API is optional - pages render their empty states
// without it; set API_URL/API_SERVICE_TOKEN to point at a live backend for real content.

import { mkdirSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = fileURLToPath(new URL('..', import.meta.url));
const OUT = fileURLToPath(new URL('../.smoke', import.meta.url));
const PORT = 4399;
const BASE = `http://127.0.0.1:${PORT}`;

// A conversation shaped exactly like lib/chat-state.svelte.ts persists it: one fast answer
// with cards and one formatted LLM answer, so both render paths appear in the screenshots.
const CONVERSATION = {
  v: 1,
  savedAt: Date.now(),
  messages: [
    { role: 'user', text: 'attention mechanisms', phase: 'done' },
    {
      role: 'assistant',
      text: '',
      phase: 'done',
      mode: 'fast',
      results: [
        {
          id: 'arxiv:2401.00001',
          title: 'Causal Discovery with Inverted Self-attention for Time Series',
          published_at: '2026-07-01T00:00:00Z',
          venue: null,
          matches: ['attention'],
          keywords: [
            'causal discovery',
            'multivariate time series',
            'self attention',
            'inverted attention',
            'causal relationships',
            'time series models',
          ],
          excerpt:
            'Attention mechanisms enable models to focus on relevant data segments and are widely used in fields like NLP and computer vision.',
          relevance: 0.7,
        },
        {
          id: 'arxiv:2401.00002',
          title: 'Efficient Long-context Attention at Scale',
          published_at: '2026-06-15T00:00:00Z',
          venue: 'NeurIPS',
          matches: ['attention', 'efficiency'],
          keywords: ['long context', 'linear attention'],
          excerpt: null,
          relevance: 0.55,
        },
      ],
      cited: ['arxiv:2401.00001', 'arxiv:2401.00002'],
      used: [
        { id: 'arxiv:2401.00001', title: 'Causal Discovery with Inverted Self-attention', score: 1 },
        { id: 'arxiv:2401.00002', title: 'Efficient Long-context Attention at Scale', score: 0.9 },
      ],
    },
    { role: 'user', text: 'summarize the main approaches', phase: 'done' },
    {
      role: 'assistant',
      phase: 'done',
      mode: 'llm',
      text: 'Two families dominate recent work [arxiv:2401.00001]:\n\n## Approaches\n- **Sparse attention** keeps a fixed budget per token\n- **Linear attention** replaces softmax with a kernel, like `phi(q) phi(k)`\n\n1. Pick the family by sequence length\n2. Measure against a dense baseline\n\nBoth trade recall for throughput.',
      cited: ['arxiv:2401.00001'],
      used: [
        { id: 'arxiv:2401.00001', title: 'Causal Discovery with Inverted Self-attention', score: 1 },
      ],
    },
  ],
};

function startServer() {
  const child = spawn('node', ['dist/server/entry.mjs'], {
    cwd: root,
    env: {
      ...process.env,
      HOST: '127.0.0.1',
      PORT: String(PORT),
      API_URL: process.env.API_URL ?? 'http://127.0.0.1:8001',
    },
    stdio: 'ignore',
  });
  return child;
}

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

const VIEWPORTS = [
  ['phone', { width: 390, height: 844 }],
  ['desktop', { width: 1280, height: 800 }],
];

async function shoot(browser, name, viewport) {
  const context = await browser.newContext({ viewport });
  await context.addInitScript(
    (envelope) => localStorage.setItem('rs-scout-chat', JSON.stringify(envelope)),
    CONVERSATION,
  );
  const page = await context.newPage();
  // 'load' plus a settle, not networkidle: the footer status island and the feed keep
  // connections busy long enough that networkidle can simply never arrive.
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/${name}-home.png`, fullPage: false });

  // The hamburger only exists below the rail tier; at desktop width the rail is the
  // always-visible column already in the home shot.
  if (viewport.width < 1024) {
    await page.click('[data-open-rail]');
    await page.waitForTimeout(700); // past the 250ms slide plus a paint or two
    await page.screenshot({ path: `${OUT}/${name}-drawer.png` });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);
  }

  if (viewport.width >= 1024) {
    // The avatar menu opens into the rail band, so this shot is the stacking fix's proof
    // (local no-auth mode always has a signed-in user, so the button is always there).
    if (await page.$('.avatar')) {
      await page.click('.avatar');
      await page.waitForTimeout(300);
      await page.screenshot({ path: `${OUT}/${name}-avatar-menu.png` });
      await page.keyboard.press('Escape');
      await page.waitForTimeout(200);
    }
    // The rail's Settings entry is visible at this width; on phones it lives inside the
    // drawer, which the drawer shot above already shows.
    await page.click('[data-open-settings]');
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${OUT}/${name}-settings.png` });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);
  }

  await page.click('.field input');
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}/${name}-chat.png` });
  await context.close();
}

// A second pass with stored preferences: dark theme, ocean accent, large type, compact
// density - seeded before load, so the shots prove the pre-paint script applies them under
// the built content policy, and the profile shot shows the avatar grid in dark.
async function shootPrefs(browser) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await context.addInitScript(() => {
    localStorage.setItem('rs-theme', 'dark');
    localStorage.setItem(
      'rs-prefs',
      JSON.stringify({ v: 1, accent: 'ocean', fontSize: 'large', density: 'compact' }),
    );
  });
  const page = await context.newPage();
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/desktop-prefs.png`, fullPage: false });
  await page.goto(`${BASE}/profile`, { waitUntil: 'load' });
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${OUT}/desktop-profile.png`, fullPage: false });
  await context.close();
}

mkdirSync(OUT, { recursive: true });
const server = startServer();
try {
  await waitForServer();
  const browser = await chromium.launch();
  for (const [name, viewport] of VIEWPORTS) {
    await shoot(browser, name, viewport);
    console.log(`captured ${name}: home, drawer or avatar menu and settings, chat`);
  }
  await shootPrefs(browser);
  console.log('captured desktop-prefs and desktop-profile (dark, ocean, large, compact)');
  await browser.close();
} finally {
  server.kill();
}
console.log(`screenshots in ${OUT}`);
