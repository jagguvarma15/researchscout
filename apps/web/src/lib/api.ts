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
  venue: string | null;
  published_at: string;
  source: string;
  url: string | null;
  pdf_url: string | null;
  score: number | null;
}

export interface FeedParams {
  q?: string;
  days?: string;
  category?: string;
  page?: number;
}

export const PAGE_SIZE = 20;

const API_URL = process.env.API_URL ?? 'http://localhost:8000';

export async function fetchPapers(params: FeedParams): Promise<PaperSummary[] | null> {
  const search = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (params.q) search.set('q', params.q);
  if (params.days) search.set('days', params.days);
  if (params.category) search.set('category', params.category);
  if (!params.q && params.page && params.page > 1) {
    search.set('offset', String((params.page - 1) * PAGE_SIZE));
  }
  try {
    const response = await fetch(`${API_URL}/v1/papers?${search}`);
    if (!response.ok) return null;
    const body = (await response.json()) as { items: PaperSummary[] };
    return body.items;
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

export function formatDate(iso: string): string {
  return iso.slice(0, 10);
}
