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
  // When the exchange happened (epoch ms), stamped on push and persisted.
  at?: number;
  // The turn ran the multi-hop deep path; the plan is its decomposed sub-questions
  // (streamed as the plan event before meta).
  agentic?: boolean;
  plan?: string[];
  // The paper pin active when the question was asked, kept on the message so a restored
  // transcript still says what a scoped answer was scoped to.
  scope?: { paperId: string; title: string };
  // Citation ids the post-check dropped because the model invented them.
  hallucinated?: string[];
  // What produced the answer and what it cost, from the enriched done event.
  model?: string;
  promptTokens?: number;
  completionTokens?: number;
  elapsedMs?: number;
  // How an errored stream failed: busy (try again shortly), quota (out for the day, fast
  // answers still work), or unavailable (the backend itself).
  errorKind?: 'busy' | 'quota' | 'unavailable';
  // A failure that arrived after text had streamed: the note renders after the preserved
  // partial answer instead of replacing it.
  errorNote?: string;
}
