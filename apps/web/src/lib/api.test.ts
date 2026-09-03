// @vitest-environment node
//
// First tests for the API client. Node rather than the project-wide jsdom: this module only
// ever runs in Astro frontmatter on the server. The network is a stubbed global fetch - what
// is pinned here is the shape contract pages rely on (CatalogResult tells apart unreachable,
// HTTP error, and data), the derived failure copy, the id encoding, and the digest readers'
// paging contract.

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  catalogMessage,
  digestQuery,
  fetchDigest,
  fetchDigests,
  fetchPaper,
  fetchPapers,
  fetchSaved,
  fetchTopics,
} from './api';

function respondWith(body: unknown, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    json: () => Promise.resolve(body),
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('catalogMessage', () => {
  it('reads a null status as an unreachable API', () => {
    expect(catalogMessage({ status: null }, 'reading list')).toBe(
      'The API is unreachable - try again in a moment.'
    );
  });

  it('reads a 404 as a backend older than the page', () => {
    expect(catalogMessage({ status: 404 }, 'topics')).toBe(
      'This backend serves no topics endpoint, so it is older than this page. Rebuild and redeploy it.'
    );
  });

  it('names any other status and where to look', () => {
    expect(catalogMessage({ status: 500 }, 'digests')).toBe(
      'The digests endpoint answered 500. The API log will say why.'
    );
  });
});

describe('the widened list readers', () => {
  it('unwrap the items envelope on success', async () => {
    vi.stubGlobal('fetch', respondWith({ items: [{ id: 'arxiv:1' }] }));
    const result = await fetchSaved('token-abc');
    expect(result).toEqual({ ok: true, data: [{ id: 'arxiv:1' }] });
  });

  it('carry the account token as a bearer header', async () => {
    const fetchMock = respondWith({ items: [] });
    vi.stubGlobal('fetch', fetchMock);
    await fetchSaved('token-abc');
    const [, init] = fetchMock.mock.calls[0] as [string, { headers: Record<string, string> }];
    expect(init.headers.authorization).toBe('Bearer token-abc');
  });

  it('report an HTTP error with its status', async () => {
    vi.stubGlobal('fetch', respondWith(null, false, 502));
    const result = await fetchTopics();
    expect(result).toEqual({ ok: false, failure: { status: 502 } });
  });

  it('report an unreachable API with a null status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('fetch failed')));
    const result = await fetchTopics();
    expect(result).toEqual({ ok: false, failure: { status: null } });
  });

  it('fetchPapers keeps the page envelope of items and total', async () => {
    vi.stubGlobal('fetch', respondWith({ items: [{ id: 'arxiv:2' }], total: 41 }));
    const result = await fetchPapers({ q: 'attention' });
    expect(result).toEqual({ ok: true, data: { items: [{ id: 'arxiv:2' }], total: 41 } });
  });
});

describe('fetchPaper', () => {
  it('encodes the id, which routinely carries a scheme and slashes', async () => {
    const fetchMock = respondWith({ id: 'doi:10.1145/3576915' });
    vi.stubGlobal('fetch', fetchMock);
    await fetchPaper('doi:10.1145/3576915');
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url.endsWith('/v1/papers/doi%3A10.1145%2F3576915')).toBe(true);
  });
});

describe('digestQuery', () => {
  it('always pins the page size and starts unfiltered', () => {
    expect(digestQuery({})).toBe('limit=20');
  });

  it('carries the kind and turns pages into offsets', () => {
    expect(digestQuery({ kind: 'weekly', page: 3 })).toBe('kind=weekly&limit=20&offset=40');
  });

  it('page one carries no offset', () => {
    expect(digestQuery({ kind: 'daily', page: 1 })).toBe('kind=daily&limit=20');
  });
});

describe('the digest readers', () => {
  it('fetchDigests keeps the page envelope', async () => {
    vi.stubGlobal(
      'fetch',
      respondWith({ items: [{ slug: '2026-w28' }], total: 41, limit: 20, offset: 0 })
    );
    const result = await fetchDigests({ kind: 'weekly' });
    expect(result).toEqual({
      ok: true,
      data: { items: [{ slug: '2026-w28' }], total: 41, limit: 20, offset: 0 },
    });
  });

  it('fetchDigests floors a missing total at the visible page', async () => {
    vi.stubGlobal('fetch', respondWith({ items: [{ slug: '2026-w28' }] }));
    const result = await fetchDigests();
    expect(result.ok && result.data.total).toBe(1);
  });

  it('fetchDigest reports failures instead of collapsing them into null', async () => {
    vi.stubGlobal('fetch', respondWith(null, false, 503));
    const result = await fetchDigest('2026-w28');
    expect(result).toEqual({ ok: false, failure: { status: 503 } });
  });

  it('fetchDigest encodes the slug', async () => {
    const fetchMock = respondWith({ slug: 'a b' });
    vi.stubGlobal('fetch', fetchMock);
    await fetchDigest('a b');
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url.endsWith('/v1/digests/a%20b')).toBe(true);
  });
});
