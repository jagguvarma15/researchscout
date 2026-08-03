// @vitest-environment node
//
// The redirect_uri handed to the identity provider is built from this. Get it wrong and the
// provider answers "Callback URL mismatch" on its own error page, which names neither the
// setting nor the value - so these pin the fallbacks rather than trusting them.

import { beforeEach, describe, expect, it, vi } from 'vitest';

async function siteUrl(env: Record<string, string | undefined>): Promise<string> {
  vi.resetModules();
  for (const key of ['SITE_URL', 'VERCEL_PROJECT_PRODUCTION_URL']) {
    if (env[key] === undefined) delete process.env[key];
    else process.env[key] = env[key];
  }
  return (await import('./site-url.js')).SITE_URL;
}

beforeEach(() => {
  vi.resetModules();
});

describe('where the deployment thinks it lives', () => {
  it('takes SITE_URL when it is set', async () => {
    expect(await siteUrl({ SITE_URL: 'https://example.test' })).toBe('https://example.test');
  });

  it('falls back to the Vercel production alias', async () => {
    // Vercel injects this into every build, so a deployment is correct unconfigured.
    const url = await siteUrl({ VERCEL_PROJECT_PRODUCTION_URL: 'researchscout.vercel.app' });
    expect(url).toBe('https://researchscout.vercel.app');
  });

  it('prefers an explicit SITE_URL over the alias', async () => {
    const url = await siteUrl({
      SITE_URL: 'https://papers.example',
      VERCEL_PROJECT_PRODUCTION_URL: 'researchscout.vercel.app',
    });
    expect(url).toBe('https://papers.example');
  });

  it('is localhost with neither', async () => {
    expect(await siteUrl({})).toBe('http://localhost:4321');
  });

  it('assumes https for a bare hostname', async () => {
    // What a dashboard shows, and therefore what gets pasted.
    expect(await siteUrl({ SITE_URL: 'researchscout.vercel.app' })).toBe(
      'https://researchscout.vercel.app',
    );
  });

  it('drops a trailing slash', async () => {
    // Otherwise the redirect_uri becomes //callback and stops matching the allow list.
    expect(await siteUrl({ SITE_URL: 'https://researchscout.vercel.app/' })).toBe(
      'https://researchscout.vercel.app',
    );
  });
});
