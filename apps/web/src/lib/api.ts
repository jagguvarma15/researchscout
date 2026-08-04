// Server-side client for the ResearchScout API. Helpers return null on any failure so pages
// can render a degraded state instead of a 500 when the API is down.

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
  minCitations?: string;
  sort?: string;
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

export async function fetchPapers(params: FeedParams): Promise<PaperPage | null> {
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
  if (!params.q && params.page && params.page > 1) {
    search.set('offset', String((params.page - 1) * PAGE_SIZE));
  }
  try {
    const response = await fetch(`${API_URL}/v1/papers?${search}`, { headers: apiHeaders() });
    if (!response.ok) return null;
    const body = (await response.json()) as { items: PaperSummary[]; total: number | null };
    return { items: body.items, total: body.total };
  } catch {
    return null;
  }
}

export async function fetchSaved(token?: string | null): Promise<PaperSummary[] | null> {
  try {
    const response = await fetch(`${API_URL}/v1/me/saved`, { headers: apiHeaders(token) });
    if (!response.ok) return null;
    const body = (await response.json()) as { items: PaperSummary[] };
    return body.items;
  } catch {
    return null;
  }
}

export async function fetchForYou(token?: string | null): Promise<PaperSummary[] | null> {
  try {
    const response = await fetch(`${API_URL}/v1/me/feed`, { headers: apiHeaders(token) });
    if (!response.ok) return null;
    const body = (await response.json()) as { items: PaperSummary[] };
    return body.items;
  } catch {
    return null;
  }
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
    const response = await fetch(`${API_URL}/v1/papers/${id}`, { headers: apiHeaders() });
    if (!response.ok) return null;
    return (await response.json()) as PaperSummary;
  } catch {
    return null;
  }
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

// The signed-in account and whether it still owes a terms acceptance. The server decides
// which version is current; the site just reports it back when the visitor accepts.
export interface Account {
  sub: string;
  username: string;
  email: string | null;
  display_name: string | null;
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
}

export interface Benchmark {
  id: string;
  name: string;
  released_on: string | null;
  result_count: number;
}

export interface BenchmarkDetail extends Benchmark {
  results: BenchmarkResult[];
}

export interface ModelParams {
  organization?: string;
  domain?: string;
  openWeights?: string;
  withPaper?: boolean;
  paperId?: string;
  page?: number;
}

export const MODEL_PAGE_SIZE = 50;

export async function fetchModels(
  params: ModelParams = {},
): Promise<{ items: AiModel[]; total: number } | null> {
  const search = new URLSearchParams({ limit: String(MODEL_PAGE_SIZE) });
  if (params.organization) search.set('organization', params.organization);
  if (params.domain) search.set('domain', params.domain);
  if (params.openWeights) search.set('open_weights', params.openWeights);
  if (params.withPaper) search.set('with_paper', 'true');
  if (params.paperId) search.set('paper_id', params.paperId);
  if (params.page && params.page > 1) {
    search.set('offset', String((params.page - 1) * MODEL_PAGE_SIZE));
  }
  try {
    const response = await fetch(`${API_URL}/v1/models?${search}`, { headers: apiHeaders() });
    if (!response.ok) return null;
    return (await response.json()) as { items: AiModel[]; total: number };
  } catch {
    return null;
  }
}

export async function fetchBenchmarks(): Promise<Benchmark[] | null> {
  try {
    const response = await fetch(`${API_URL}/v1/benchmarks`, { headers: apiHeaders() });
    if (!response.ok) return null;
    const body = (await response.json()) as { items: Benchmark[] };
    return body.items;
  } catch {
    return null;
  }
}

export async function fetchBenchmark(id: string): Promise<BenchmarkDetail | null> {
  try {
    const response = await fetch(`${API_URL}/v1/benchmarks/${id}?limit=25`, {
      headers: apiHeaders(),
    });
    if (!response.ok) return null;
    return (await response.json()) as BenchmarkDetail;
  } catch {
    return null;
  }
}

// --- Per-account site state (signed in only; a cache, so failures cost a suggestion) ---

export async function fetchSearchHistory(token?: string | null): Promise<string[] | null> {
  try {
    const response = await fetch(`${API_URL}/v1/me/history`, { headers: apiHeaders(token) });
    if (!response.ok) return null;
    const body = (await response.json()) as { items: string[] };
    return body.items;
  } catch {
    return null;
  }
}

export async function fetchDismissals(token?: string | null): Promise<string[] | null> {
  try {
    const response = await fetch(`${API_URL}/v1/me/dismissals`, { headers: apiHeaders(token) });
    if (!response.ok) return null;
    const body = (await response.json()) as { items: string[] };
    return body.items;
  } catch {
    return null;
  }
}

export interface DigestSummary {
  slug: string;
  title: string;
  period_start: string;
  period_end: string;
}

export interface DigestDetail extends DigestSummary {
  body: string;
  items: { paper_id: string; title: string; score: number; citations: number }[];
}

export async function fetchDigests(): Promise<DigestSummary[] | null> {
  try {
    const response = await fetch(`${API_URL}/v1/digests`, { headers: apiHeaders() });
    if (!response.ok) return null;
    const body = (await response.json()) as { items: DigestSummary[] };
    return body.items;
  } catch {
    return null;
  }
}

export async function fetchDigest(slug: string): Promise<DigestDetail | null> {
  try {
    const response = await fetch(`${API_URL}/v1/digests/${slug}`, { headers: apiHeaders() });
    if (!response.ok) return null;
    return (await response.json()) as DigestDetail;
  } catch {
    return null;
  }
}

export interface TopicPaper {
  paper_id: string;
  title: string;
  score: number;
}

export interface TopicDetail {
  id: number;
  label: string;
  summary: string | null;
  score: number;
  size: number;
  trend: string | null;
  papers: TopicPaper[];
}

export async function fetchTopics(): Promise<TopicDetail[] | null> {
  try {
    const response = await fetch(`${API_URL}/v1/topics`, { headers: apiHeaders() });
    if (!response.ok) return null;
    const body = (await response.json()) as { items: TopicDetail[] };
    return body.items;
  } catch {
    return null;
  }
}

// Digest bodies are plain LLM text: escape everything, then turn [scheme:id] citations into
// paper links and blank lines into paragraph breaks. Only ids actually in the digest get
// linked — an id the model invented stays as escaped plain text instead of a dead link.
export function renderDigestBody(text: string, validIds: Set<string>): string {
  const escaped = text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
  const linked = escaped.replace(/\[([a-z]+:[^\]\s]+)\]/g, (match, id: string) =>
    validIds.has(id) ? `<a href="/papers/${id}">[${id}]</a>` : match,
  );
  return linked
    .split(/\n{2,}/)
    .map((block) => `<p>${block.replaceAll('\n', '<br />')}</p>`)
    .join('');
}
