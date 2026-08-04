// Shared chat data shapes: the drawer components and the tested helper modules import
// from here so SSE payloads and UI state stay one set of definitions.

export interface UsedPaper {
  id: string;
  title: string;
  score: number;
}

export interface WebHit {
  provider: string;
  title: string;
  authors: string[];
  year: number | null;
  snippet: string;
  arxiv_id: string | null;
  url: string | null;
  already_known: boolean;
  paper_id: string | null;
}

// Mirror of the backend FastResultItem streamed as the fast-mode results event.
export interface FastResult {
  id: string;
  title: string;
  published_at: string;
  venue: string | null;
  matches: string[];
  keywords: string[];
  excerpt: string | null;
  relevance: number | null;
}

// One row of GET /v1/keywords: a corpus keyword and how many papers carry it.
export interface KeywordCount {
  keyword: string;
  papers: number;
}

export interface Message {
  role: 'user' | 'assistant';
  text: string;
  phase?: 'searching' | 'thinking' | 'streaming' | 'done';
  mode?: 'fast' | 'llm';
  question?: string;
  retrieved?: number;
  cited?: string[];
  used?: UsedPaper[];
  error?: boolean;
  // The request needed an account. Not an error: the quick path is open to everyone and
  // only generated answers, web search and import are not, so this offers the way in.
  needsSignIn?: boolean;
  // The stream was aborted by the Stop button; partial text is kept.
  stopped?: boolean;
  // Structured fast-mode results; when present the raw token text is not rendered.
  results?: FastResult[];
  // Dictionary keywords the question hit, shown in the loader line.
  matched?: { keywords: string[]; papers: number } | null;
  notfound?: { query: string; webSearch: boolean };
  webBusy?: boolean;
  webHits?: WebHit[];
  webFailed?: string[];
  imports?: Record<string, string>;
}
