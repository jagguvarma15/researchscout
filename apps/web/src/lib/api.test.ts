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
  fetchBenchmark,
  fetchBenchmarks,
  fetchCatalogFreshness,
  fetchDigest,
  fetchDigests,
  fetchForYou,
  fetchModels,
  fetchNotableModels,
  fetchPaper,
  fetchPapers,
  fetchProviders,
  fetchSaved,
  fetchSavedIds,
  fetchTopic,
  fetchTopics,
  fetchTrends,
  formatScore,
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

describe('fetchForYou', () => {
  it('returns the whole envelope, keeping the profile block', async () => {
    const feed = { items: [{ id: 'arxiv:1' }], profile: { interests: 2, saves: 3, reads: 1, centroids: 2 } };
    vi.stubGlobal('fetch', respondWith(feed));
    const result = await fetchForYou('t');
    expect(result).toEqual({ ok: true, data: feed });
  });

  it('builds the days and limit query', async () => {
    const fetchMock = respondWith({ items: [] });
    vi.stubGlobal('fetch', fetchMock);
    await fetchForYou('t', { days: 7, limit: 10 });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url.endsWith('/v1/me/feed?days=7&limit=10')).toBe(true);
  });

  it('a bare call hits the plain feed path', async () => {
    const fetchMock = respondWith({ items: [] });
    vi.stubGlobal('fetch', fetchMock);
    await fetchForYou('t');
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url.endsWith('/v1/me/feed')).toBe(true);
  });
});

describe('fetchSavedIds', () => {
  it('unwraps the ids envelope', async () => {
    vi.stubGlobal('fetch', respondWith({ ids: ['arxiv:1', 'arxiv:2'] }));
    const result = await fetchSavedIds('t');
    expect(result).toEqual({ ok: true, data: ['arxiv:1', 'arxiv:2'] });
  });

  it('passes the failure through (an old API 404s the route)', async () => {
    vi.stubGlobal('fetch', respondWith(null, false, 404));
    const result = await fetchSavedIds('t');
    expect(result).toEqual({ ok: false, failure: { status: 404 } });
  });
});

describe('readCatalog timeout', () => {
  it('carries an abort signal so a hung API cannot stall the render', async () => {
    const fetchMock = respondWith({ items: [] });
    vi.stubGlobal('fetch', fetchMock);
    await fetchSavedIds('t');
    const [, init] = fetchMock.mock.calls[0] as [string, { signal?: AbortSignal }];
    expect(init.signal).toBeInstanceOf(AbortSignal);
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

describe('formatScore', () => {
  it('shows a fraction as a one-decimal number (the page adds the percent sign)', () => {
    expect(formatScore(0.947, 'fraction')).toBe('94.7');
  });

  it('groups a large raw score rather than multiplying it (Vending Bench was 1118187.3)', () => {
    expect(formatScore(1118187.3, 'raw')).toBe('1,118,187');
  });

  it('keeps a small raw score to two decimals', () => {
    expect(formatScore(87.5, 'raw')).toBe('87.50');
  });
});

describe('fetchTrends', () => {
  it('returns the sota and releases payload, carrying model ids on frontier points', async () => {
    const body = {
      sota: [
        {
          id: 'gpqa-diamond',
          name: 'GPQA Diamond',
          scale: 'fraction',
          points: [{ on: '2025-01-01', score: 0.5, model_name: 'X', model_id: 'x-1' }],
        },
      ],
      releases: [],
    };
    vi.stubGlobal('fetch', respondWith(body));
    expect(await fetchTrends()).toEqual({ ok: true, data: body });
  });
});

describe('fetchModels', () => {
  it('always pins the page size and omits the default sort', async () => {
    const fetchMock = respondWith({ items: [], total: 0 });
    vi.stubGlobal('fetch', fetchMock);
    await fetchModels({ sort: 'released' });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url.endsWith('/v1/models?limit=50')).toBe(true);
  });

  it('carries filters and turns a page into an offset', async () => {
    const fetchMock = respondWith({ items: [], total: 0 });
    vi.stubGlobal('fetch', fetchMock);
    await fetchModels({ organization: 'OpenAI', sort: 'downloads', page: 3 });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toContain('organization=OpenAI');
    expect(url).toContain('sort=downloads');
    expect(url).toContain('offset=100');
  });

  it('keeps the items and total envelope', async () => {
    vi.stubGlobal('fetch', respondWith({ items: [{ id: 'm1' }], total: 7 }));
    expect(await fetchModels()).toEqual({ ok: true, data: { items: [{ id: 'm1' }], total: 7 } });
  });
});

describe('the catalogue readers', () => {
  it('fetchBenchmarks unwraps the items envelope', async () => {
    vi.stubGlobal('fetch', respondWith({ items: [{ id: 'mmlu' }] }));
    expect(await fetchBenchmarks()).toEqual({ ok: true, data: [{ id: 'mmlu' }] });
  });

  it('fetchBenchmark encodes the id and asks for a leaderboard page', async () => {
    const fetchMock = respondWith({ id: 'a b', results: [] });
    vi.stubGlobal('fetch', fetchMock);
    await fetchBenchmark('a b');
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url.endsWith('/v1/benchmarks/a%20b?limit=50')).toBe(true);
  });

  it('fetchProviders returns the columns-and-items table', async () => {
    const table = { columns: [{ id: 'mmlu', name: 'MMLU', scale: 'fraction' }], items: [] };
    vi.stubGlobal('fetch', respondWith(table));
    expect(await fetchProviders()).toEqual({ ok: true, data: table });
  });

  it('fetchNotableModels unwraps the items envelope', async () => {
    vi.stubGlobal('fetch', respondWith({ items: [{ id: 'x' }] }));
    expect(await fetchNotableModels()).toEqual({ ok: true, data: [{ id: 'x' }] });
  });
});

describe('fetchTopic', () => {
  it('returns a result on success, not a bare object', async () => {
    vi.stubGlobal('fetch', respondWith({ id: 5, label: 'Diffusion' }));
    expect(await fetchTopic(5)).toEqual({ ok: true, data: { id: 5, label: 'Diffusion' } });
  });

  it('reports a 404 so a missing topic is not confused with an unreachable API', async () => {
    vi.stubGlobal('fetch', respondWith(null, false, 404));
    expect(await fetchTopic(9)).toEqual({ ok: false, failure: { status: 404 } });
  });

  it('reports an unreachable API with a null status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('down')));
    expect(await fetchTopic(9)).toEqual({ ok: false, failure: { status: null } });
  });
});

describe('fetchCatalogFreshness', () => {
  it('returns the freshness payload', async () => {
    const body = {
      models_at: '2026-09-01T00:00:00Z',
      benchmarks_at: '2026-09-02T00:00:00Z',
      topics_at: null,
      as_of: '2026-09-02T00:00:00Z',
    };
    vi.stubGlobal('fetch', respondWith(body));
    expect(await fetchCatalogFreshness()).toEqual({ ok: true, data: body });
  });
});
