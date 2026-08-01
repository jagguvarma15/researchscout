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
  kind?: string;
  group?: string[];
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

export async function fetchPapers(params: FeedParams): Promise<PaperPage | null> {
  const search = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (params.q) search.set('q', params.q);
  if (params.days) search.set('days', params.days);
  if (params.year) search.set('year', params.year);
  if (params.month) search.set('month', params.month);
  if (params.kind) search.set('kind', params.kind);
  if (params.author) search.set('author', params.author);
  if (params.venue) search.set('venue', params.venue);
  if (params.minCitations) search.set('min_citations', params.minCitations);
  if (params.sort && params.sort !== 'newest') search.set('sort', params.sort);
  for (const value of params.category ?? []) search.append('category', value);
  for (const value of params.group ?? []) search.append('group', value);
  if (!params.q && params.page && params.page > 1) {
    search.set('offset', String((params.page - 1) * PAGE_SIZE));
  }
  try {
    const response = await fetch(`${API_URL}/v1/papers?${search}`);
    if (!response.ok) return null;
    const body = (await response.json()) as { items: PaperSummary[]; total: number | null };
    return { items: body.items, total: body.total };
  } catch {
    return null;
  }
}

export async function fetchSaved(): Promise<PaperSummary[] | null> {
  try {
    const response = await fetch(`${API_URL}/v1/me/saved`);
    if (!response.ok) return null;
    const body = (await response.json()) as { items: PaperSummary[] };
    return body.items;
  } catch {
    return null;
  }
}

export async function fetchForYou(): Promise<PaperSummary[] | null> {
  try {
    const response = await fetch(`${API_URL}/v1/me/feed`);
    if (!response.ok) return null;
    const body = (await response.json()) as { items: PaperSummary[] };
    return body.items;
  } catch {
    return null;
  }
}

export async function fetchInterests(): Promise<string[] | null> {
  try {
    const response = await fetch(`${API_URL}/v1/me/interests`);
    if (!response.ok) return null;
    const body = (await response.json()) as { interests: string[] };
    return body.interests;
  } catch {
    return null;
  }
}

export async function fetchPaper(id: string): Promise<PaperSummary | null> {
  try {
    const response = await fetch(`${API_URL}/v1/papers/${id}`);
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
    const response = await fetch(`${API_URL}/v1/sources`);
    if (!response.ok) return null;
    const body = (await response.json()) as { items: SourceInfo[] };
    return body.items;
  } catch {
    return null;
  }
}

export function formatDate(iso: string): string {
  return iso.slice(0, 10);
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
    const response = await fetch(`${API_URL}/v1/digests`);
    if (!response.ok) return null;
    const body = (await response.json()) as { items: DigestSummary[] };
    return body.items;
  } catch {
    return null;
  }
}

export async function fetchDigest(slug: string): Promise<DigestDetail | null> {
  try {
    const response = await fetch(`${API_URL}/v1/digests/${slug}`);
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
    const response = await fetch(`${API_URL}/v1/topics`);
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
