// Server-side client for the ResearchScout API. The list and catalogue readers return a
// CatalogResult so pages can tell an unreachable API from an HTTP error from an empty
// shelf and say so; the remaining helpers return null on any failure, which reads as a
// quietly degraded page rather than a 500 when the API is down.

export interface Author {
  name: string;
  affiliation: string | null;
}

export interface PaperSummary {
  id: string;
  title: string;
  abstract: string;
  authors: Author[];
  categories: string[];
  primary_category: string | null;
  venue: string | null;
  comment: string | null;
  citation_count: number;
  published_at: string;
  source: string;
  url: string | null;
  pdf_url: string | null;
  // Stream enrichment (null until the categorize stage has seen the paper).
  keywords?: string[] | null;
  labels?: { label: string; source: string; score?: number | null }[] | null;
  score: number | null;
  // Why the personalized feed picked this paper (null everywhere else).
  reason?: string | null;
}

export interface FeedParams {
  q?: string;
  days?: string;
  year?: string;
  month?: string;
  category?: string[];
  /** The field a paper is in; repeat to widen. */
  subject?: string[];
  /** The technique it uses; repeat to widen. Narrows against subject. */
  topic?: string[];
  author?: string;
  venue?: string;
  /** Extracted keyword phrases; repeat to widen. */
  keyword?: string[];
  minCitations?: string;
  sort?: string;
  /** 'asc' or 'desc'; omitted takes the column's natural direction. */
  direction?: string;
  page?: number;
}

export interface PaperPage {
  items: PaperSummary[];
  total: number | null;
}

export const PAGE_SIZE = 20;

const API_URL = process.env.API_URL ?? 'http://localhost:8000';
// The shared secret that gets this deployment past the API's front door: unset locally, where
// the API is open. Server-side only - it never reaches a browser.
const SERVICE_TOKEN = process.env.API_SERVICE_TOKEN ?? '';

/** Headers for a server-side API call: the caller's account token, plus the service token. */
export function apiHeaders(token?: string | null): Record<string, string> {
  const headers: Record<string, string> = {};
  if (token) headers.authorization = `Bearer ${token}`;
  if (SERVICE_TOKEN) headers['x-rs-service-token'] = SERVICE_TOKEN;
  return headers;
}

export async function fetchPapers(params: FeedParams): Promise<CatalogResult<PaperPage>> {
  const search = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (params.q) search.set('q', params.q);
  if (params.days) search.set('days', params.days);
  if (params.year) search.set('year', params.year);
  if (params.month) search.set('month', params.month);
  if (params.author) search.set('author', params.author);
  if (params.venue) search.set('venue', params.venue);
  if (params.minCitations) search.set('min_citations', params.minCitations);
  if (params.sort && params.sort !== 'newest') search.set('sort', params.sort);
  for (const value of params.category ?? []) search.append('category', value);
  for (const value of params.subject ?? []) search.append('subject', value);
  for (const value of params.topic ?? []) search.append('topic', value);
  for (const value of params.keyword ?? []) search.append('keyword', value);
  if (!params.q && params.page && params.page > 1) {
    search.set('offset', String((params.page - 1) * PAGE_SIZE));
  }
  const result = await readCatalog<{ items: PaperSummary[]; total: number | null }>(
    `/v1/papers?${search}`,
  );
  return result.ok
    ? { ok: true, data: { items: result.data.items, total: result.data.total } }
    : result;
}

export async function fetchSaved(token?: string | null): Promise<CatalogResult<PaperSummary[]>> {
  const result = await readCatalog<{ items: PaperSummary[] }>('/v1/me/saved', token);
  return result.ok ? { ok: true, data: result.data.items } : result;
}

// Just the saved paper ids - what the feed pages need to light save buttons, without pulling
// the whole library. A pre-wave backend serves no such route; the 404 falls into the failure
// path and callers render an empty saved set, exactly as they already tolerate for fetchSaved.
export async function fetchSavedIds(token?: string | null): Promise<CatalogResult<string[]>> {
  const result = await readCatalog<{ ids: string[] }>('/v1/me/saved/ids', token);
  return result.ok ? { ok: true, data: result.data.ids } : result;
}

// The reading list with its library fields - what /saved renders. fetchSaved above stays
// for the pages that only need the id set.
export interface SavedPaperItem extends PaperSummary {
  status: 'to-read' | 'reading' | 'done';
  tags: string[];
  note: string | null;
  saved_at: string | null;
}

export interface SavedLibrary {
  items: SavedPaperItem[];
  /** Every tag the reader uses, for the chips over the list. */
  tags: string[];
}

export interface SavedListParams {
  status?: string;
  tag?: string;
  q?: string;
  sort?: string;
}

export async function fetchSavedList(
  params: SavedListParams,
  token?: string | null,
): Promise<CatalogResult<SavedLibrary>> {
  const search = new URLSearchParams();
  if (params.status) search.set('status', params.status);
  if (params.tag) search.set('tag', params.tag);
  if (params.q) search.set('q', params.q);
  if (params.sort && params.sort !== 'saved') search.set('sort', params.sort);
  const query = search.toString();
  return readCatalog<SavedLibrary>(`/v1/me/saved${query ? `?${query}` : ''}`, token);
}

/* The reader's profile shape, for the For You transparency header. */
export interface FeedProfileInfo {
  interests: number;
  saves: number;
  reads: number;
  centroids: number;
}

/* The feed envelope: the ranked papers plus, when there is a profile, its shape. `profile` is
   absent on cold start and from a pre-wave backend; both this and the old page tolerate both. */
export interface ForYouFeed {
  items: PaperSummary[];
  profile?: FeedProfileInfo | null;
}

export async function fetchForYou(
  token?: string | null,
  opts: { days?: number; limit?: number } = {},
): Promise<CatalogResult<ForYouFeed>> {
  const search = new URLSearchParams();
  if (opts.days) search.set('days', String(opts.days));
  if (opts.limit) search.set('limit', String(opts.limit));
  const query = search.toString();
  return readCatalog<ForYouFeed>(`/v1/me/feed${query ? `?${query}` : ''}`, token);
}

export async function fetchInterests(token?: string | null): Promise<string[] | null> {
  try {
    const response = await fetch(`${API_URL}/v1/me/interests`, { headers: apiHeaders(token) });
    if (!response.ok) return null;
    const body = (await response.json()) as { interests: string[] };
    return body.interests;
  } catch {
    return null;
  }
}

export async function fetchPaper(id: string): Promise<PaperSummary | null> {
  try {
    // Encoded because canonical ids carry scheme separators and slashes as a matter of
    // course - doi:10.1145/3576915 is a normal id, not an edge case.
    const response = await fetch(`${API_URL}/v1/papers/${encodeURIComponent(id)}`, {
      headers: apiHeaders(),
    });
    if (!response.ok) return null;
    return (await response.json()) as PaperSummary;
  } catch {
    return null;
  }
}

// A paper's neighborhood: stored works it references, stored works citing it, and its
// embedding nearest neighbors. Every entry carries breakthrough momentum in score, the
// same scale the detail endpoint serves, so one meter normalization covers the page.
export interface RelatedPapers {
  references: PaperSummary[];
  cited_by: PaperSummary[];
  similar: PaperSummary[];
}

export async function fetchRelated(id: string): Promise<RelatedPapers | null> {
  try {
    const response = await fetch(`${API_URL}/v1/papers/${encodeURIComponent(id)}/related`, {
      headers: apiHeaders(),
    });
    if (!response.ok) return null;
    return (await response.json()) as RelatedPapers;
  } catch {
    return null;
  }
}

// The trends payload: benchmark frontiers over time plus the recent notable releases.
export interface SotaPoint {
  on: string;
  score: number;
  model_name: string;
}

export interface SotaSeries {
  id: string;
  name: string;
  scale: string;
  points: SotaPoint[];
}

export interface TrendsData {
  sota: SotaSeries[];
  releases: NotableModel[];
}

export async function fetchTrends(): Promise<CatalogResult<TrendsData>> {
  return readCatalog<TrendsData>('/v1/trends');
}

// A data source as /about lists it. The attribution fields are null together when a source
// has not declared one in config/sources.yaml, which the page shows rather than hides.
export interface SourceInfo {
  name: string;
  kind: string;
  enabled: boolean;
  display_name: string | null;
  homepage: string | null;
  terms_url: string | null;
  data_license: string | null;
  provides: string | null;
}

export async function fetchSources(): Promise<SourceInfo[] | null> {
  try {
    const response = await fetch(`${API_URL}/v1/sources`, { headers: apiHeaders() });
    if (!response.ok) return null;
    const body = (await response.json()) as { items: SourceInfo[] };
    return body.items;
  } catch {
    return null;
  }
}

// The deployment's own account of itself: freshness, recent runs, self-checks, schedule.
// The about page renders it and the footer reads the freshness line.
export interface SchedulerRun {
  task: string;
  started_at: string;
  finished_at: string | null;
  ok: boolean;
  note: string;
}

export interface HealthCheckInfo {
  name: string;
  status: string;
  detail: string;
}

export interface ScheduleGroup {
  group: string;
  at: string[];
  timezone: string;
  next_run: string | null;
}

export interface AskStats {
  days: number;
  asked: number;
  found_rate: number | null;
  fast_p50_ms: number | null;
  fast_p95_ms: number | null;
  llm_p50_ms: number | null;
  llm_p95_ms: number | null;
  notfound: string[];
  // Outcome counts and the hallucination rate; optional so an older API still parses.
  refused?: number;
  llm_errors?: number;
  busy?: number;
  hallucination_rate?: number | null;
}

export interface LlmPurposeCalls {
  purpose: string;
  calls: number;
  ok: number;
  quota: number;
  errors: number;
  prompt_tokens: number;
  completion_tokens: number;
}

// Today's model spend from the llm_usage ledger - the daily-budget view.
export interface LlmStats {
  model: string;
  calls_today: number;
  prompt_tokens_today: number;
  completion_tokens_today: number;
  by_purpose: LlmPurposeCalls[];
  last_quota_at: string | null;
}

export interface FeedStats {
  days: number;
  requests: number;
  p50_ms: number | null;
  p95_ms: number | null;
  cache_hit_rate: number | null;
}

export interface SystemStatus {
  version: string;
  build_sha: string | null;
  migration: string | null;
  papers: number;
  newest_paper_at: string | null;
  newest_paper_created_at: string | null;
  runs: SchedulerRun[];
  pipeline_due_at: string | null;
  scheduler_started_at: string | null;
  health: HealthCheckInfo[];
  last_health_run: SchedulerRun | null;
  schedule: ScheduleGroup[];
  // Ask/chat usage over the last week; null when nothing was asked (or an older API).
  ask?: AskStats | null;
  // Today's model spend; null when no calls today (or an older API).
  llm?: LlmStats | null;
  // For You render latency over the last week; null when nothing rendered (or an older API).
  feed?: FeedStats | null;
}

export async function fetchSystemStatus(): Promise<SystemStatus | null> {
  try {
    const response = await fetch(`${API_URL}/v1/system/status`, { headers: apiHeaders() });
    if (!response.ok) return null;
    return (await response.json()) as SystemStatus;
  } catch {
    return null;
  }
}

// The signed-in account and whether it still owes a terms acceptance. The server decides
// which version is current; the site just reports it back when the visitor accepts.
export interface Account {
  sub: string;
  username: string;
  email: string | null;
  display_name: string | null;
  avatar: string | null;
  terms_required: string;
  terms_accepted_version: string | null;
  terms_accepted: boolean;
}

export async function fetchAccount(token?: string | null): Promise<Account | null> {
  try {
    const response = await fetch(`${API_URL}/v1/me`, { headers: apiHeaders(token) });
    if (!response.ok) return null;
    return (await response.json()) as Account;
  } catch {
    return null;
  }
}

export function formatDate(iso: string): string {
  return iso.slice(0, 10);
}

// --- The AI landscape ---

export interface AiModel {
  id: string;
  name: string;
  organization: string | null;
  publication_date: string | null;
  domains: string[];
  task: string | null;
  parameters: number | null;
  training_compute_flop: number | null;
  accessibility: string | null;
  open_weights: boolean | null;
  link: string | null;
  // The paper this model came from, when this corpus holds it.
  paper_id: string | null;
  hf_repo: string | null;
  hf_downloads: number | null;
  hf_likes: number | null;
  sources: string[];
  scores: BenchmarkResult[];
}

export interface BenchmarkResult {
  benchmark: string;
  model: string;
  model_id: string | null;
  score: number;
  measured_on: string | null;
  origin: string | null;
  /** The benchmark's scale; see formatScore. */
  scale: string;
}

export interface Benchmark {
  id: string;
  name: string;
  released_on: string | null;
  result_count: number;
  /** "fraction" when the scores read as percentages, "raw" when they do not. */
  score_scale: string;
}

/**
 * A benchmark score as it should be shown, given the scale the server recorded for it.
 *
 * Most benchmarks are accuracies between zero and one, and multiplying those by a hundred was
 * done unconditionally - which is right for them and nonsense for the eleven that are a ratio,
 * an Elo or an amount of money. Vending Bench rendered as 1118187.3.
 */
export function formatScore(score: number, scale: string): string {
  if (scale === 'fraction') return (score * 100).toFixed(1);
  if (Math.abs(score) >= 1000) return score.toLocaleString('en-US', { maximumFractionDigits: 0 });
  return score.toFixed(2);
}

export interface BenchmarkDetail extends Benchmark {
  results: BenchmarkResult[];
}

export interface ModelParams {
  q?: string;
  organization?: string;
  domain?: string;
  openWeights?: string;
  withPaper?: boolean;
  paperId?: string;
  sort?: string;
  /** "asc" or "desc"; omitted takes the column's natural direction. */
  direction?: string;
  page?: number;
}

export const MODEL_PAGE_SIZE = 50;
export const MODEL_SORTS = [
  'released',
  'parameters',
  'compute',
  'downloads',
  'organization',
  'name',
] as const;
export type ModelSort = (typeof MODEL_SORTS)[number];

/**
 * Why a list or catalogue read came back with nothing.
 *
 * These helpers used to return null for every failure alike, so "the API is unreachable" was
 * what a page said whether the backend was down, older than the page, or simply had an empty
 * shelf. Those want four different things done about them, and the status is what tells
 * them apart. `status` is null when the request never reached the API at all.
 */
export interface CatalogFailure {
  status: number | null;
}

export type CatalogResult<T> = { ok: true; data: T } | { ok: false; failure: CatalogFailure };

/** What to tell a reader about a failed read, and what it means for whoever runs it. */
export function catalogMessage(failure: CatalogFailure, what: string): string {
  if (failure.status === null) return 'The API is unreachable - try again in a moment.';
  if (failure.status === 404) {
    return `This backend serves no ${what} endpoint, so it is older than this page. Rebuild and redeploy it.`;
  }
  return `The ${what} endpoint answered ${failure.status}. The API log will say why.`;
}

// A hung API used to stall SSR (and so TTFB) until the platform killed the function; the
// timeout turns that into the same degraded-page path a connection failure already takes.
const CATALOG_TIMEOUT_MS = 10_000;

async function readCatalog<T>(path: string, token?: string | null): Promise<CatalogResult<T>> {
  try {
    const response = await fetch(`${API_URL}${path}`, {
      headers: apiHeaders(token),
      signal: AbortSignal.timeout(CATALOG_TIMEOUT_MS),
    });
    if (!response.ok) return { ok: false, failure: { status: response.status } };
    return { ok: true, data: (await response.json()) as T };
  } catch {
    return { ok: false, failure: { status: null } };
  }
}

export async function fetchModels(
  params: ModelParams = {},
): Promise<CatalogResult<{ items: AiModel[]; total: number }>> {
  const search = new URLSearchParams({ limit: String(MODEL_PAGE_SIZE) });
  if (params.q) search.set('q', params.q);
  if (params.organization) search.set('organization', params.organization);
  if (params.domain) search.set('domain', params.domain);
  if (params.openWeights) search.set('open_weights', params.openWeights);
  if (params.withPaper) search.set('with_paper', 'true');
  if (params.paperId) search.set('paper_id', params.paperId);
  if (params.sort && params.sort !== 'released') search.set('sort', params.sort);
  if (params.direction) search.set('direction', params.direction);
  if (params.page && params.page > 1) {
    search.set('offset', String((params.page - 1) * MODEL_PAGE_SIZE));
  }
  return readCatalog(`/v1/models?${search}`);
}

export async function fetchModel(id: string): Promise<CatalogResult<AiModel>> {
  return readCatalog(`/v1/models/${encodeURIComponent(id)}`);
}

export async function fetchBenchmarks(): Promise<CatalogResult<Benchmark[]>> {
  const result = await readCatalog<{ items: Benchmark[] }>('/v1/benchmarks');
  return result.ok ? { ok: true, data: result.data.items } : result;
}

export async function fetchBenchmark(id: string): Promise<CatalogResult<BenchmarkDetail>> {
  return readCatalog(`/v1/benchmarks/${encodeURIComponent(id)}?limit=50`);
}

/** One row per provider: its current flagship and how that scores on the headline benchmarks. */
export interface ProviderRow {
  provider: string;
  country: string | null;
  model_id: string;
  model_name: string;
  published_on: string | null;
  paper_id: string | null;
  open_weights: boolean | null;
  scores: Record<string, number>;
}

export interface ProviderTable {
  columns: { id: string; name: string; scale: string }[];
  items: ProviderRow[];
}

export async function fetchProviders(): Promise<CatalogResult<ProviderTable>> {
  return readCatalog('/v1/providers');
}

/** One row of the recent-models strip: a curated lab's model and its headline facts. */
export interface NotableModel {
  id: string;
  name: string;
  provider: string;
  country: string | null;
  published_on: string | null;
  parameters: number | null;
  open_weights: boolean | null;
}

export async function fetchNotableModels(): Promise<CatalogResult<NotableModel[]>> {
  const result = await readCatalog<{ items: NotableModel[] }>('/v1/models/notable');
  return result.ok ? { ok: true, data: result.data.items } : result;
}

/** One curated benchmark with the best curated-lab score and who holds it. */
export interface HeadlineBenchmark {
  id: string;
  name: string;
  scale: string;
  result_count: number;
  best_score: number;
  model_id: string;
  model_name: string;
  provider: string;
}

export async function fetchHeadlineBenchmarks(): Promise<CatalogResult<HeadlineBenchmark[]>> {
  const result = await readCatalog<{ items: HeadlineBenchmark[] }>('/v1/benchmarks/headline');
  return result.ok ? { ok: true, data: result.data.items } : result;
}

// --- Per-account site state (signed in only; a cache, so failures cost a suggestion) ---

/**
 * The feed query string this account last applied, or null.
 *
 * Offered back rather than redirected to. A bare "/" that bounced to the saved filters would
 * be a trap: pressing back returns to "/", which bounces again, so the unfiltered feed becomes
 * unreachable. A link is one click and no surprises.
 */
export async function fetchSavedFilters(token?: string | null): Promise<string | null> {
  try {
    const response = await fetch(`${API_URL}/v1/me/filters`, { headers: apiHeaders(token) });
    if (!response.ok) return null;
    const body = (await response.json()) as { query_string: string | null };
    return body.query_string || null;
  } catch {
    return null;
  }
}

export interface DigestSummary {
  slug: string;
  /* weekly | daily; defaulted by the API for rows older than the column. */
  kind: string;
  title: string;
  period_start: string;
  period_end: string;
  /* Papers in the issue; 0 on rows stored before the field existed. */
  item_count: number;
  /* False when the weekly prose is the deterministic fallback. */
  llm_ok: boolean;
}

/* One ranked paper of an issue; the enrichment fields are empty on legacy rows. */
export interface DigestItem {
  paper_id: string;
  title: string;
  score: number;
  citations: number;
  primary_category?: string | null;
  keywords?: string[];
  authors?: string[];
  author_count?: number;
  venue?: string | null;
  /* Per-signal-type breakthrough contributions - why the paper ranked. */
  why?: Record<string, number>;
}

export interface DigestDetail extends DigestSummary {
  body: string;
  items: DigestItem[];
}

export const DIGEST_PAGE_SIZE = 20;

export interface DigestPage {
  items: DigestSummary[];
  total: number;
  limit: number;
  offset: number;
}

/* Pure so the archive page's tab/pager links stay testable. */
export function digestQuery(params: { kind?: string; page?: number }): string {
  const search = new URLSearchParams();
  if (params.kind) search.set('kind', params.kind);
  search.set('limit', String(DIGEST_PAGE_SIZE));
  if (params.page && params.page > 1) {
    search.set('offset', String((params.page - 1) * DIGEST_PAGE_SIZE));
  }
  return search.toString();
}

export async function fetchDigests(
  params: { kind?: string; page?: number } = {},
): Promise<CatalogResult<DigestPage>> {
  const result = await readCatalog<DigestPage>(`/v1/digests?${digestQuery(params)}`);
  if (!result.ok) return result;
  // An API one deploy older than this page serves no total; the visible page is the floor.
  return {
    ok: true,
    data: { ...result.data, total: result.data.total ?? result.data.items.length },
  };
}

export async function fetchDigest(slug: string): Promise<CatalogResult<DigestDetail>> {
  return readCatalog(`/v1/digests/${encodeURIComponent(slug)}`);
}

export interface TopicPaper {
  paper_id: string;
  title: string;
  score: number;
}

export interface TopicHistoryPoint {
  built_at: string;
  size: number;
}

export interface TopicDetail {
  id: number;
  label: string;
  summary: string | null;
  score: number;
  size: number;
  trend: string | null;
  /* Cluster size per build, oldest first — the sparkline behind the trend word. */
  history: TopicHistoryPoint[];
  papers: TopicPaper[];
}

export async function fetchTopics(): Promise<CatalogResult<TopicDetail[]>> {
  const result = await readCatalog<{ items: TopicDetail[] }>('/v1/topics');
  return result.ok ? { ok: true, data: result.data.items } : result;
}

export async function fetchTopic(id: number): Promise<TopicDetail | null> {
  try {
    const response = await fetch(`${API_URL}/v1/topics/${id}`, { headers: apiHeaders() });
    if (!response.ok) return null;
    return (await response.json()) as TopicDetail;
  } catch {
    return null;
  }
}

