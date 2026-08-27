// The Scout conversation: state and transport, hoisted out of the panel component.
//
// It lives here rather than in ScoutPanel because the panel is mounted inside the omnibox's
// `{#if open}` - closing the panel used to destroy the transcript, orphan any stream still
// running, and leave the Stop button wired to a dead component. Module state survives all of
// that: closing the panel mid-answer lets the answer keep streaming, and reopening shows it.
//
// The transcript also survives a reload: it is mirrored to localStorage (debounced, flushed
// on pagehide) and restored for a day. Restored data is treated as untrusted input - it
// feeds hrefs and, through the `used` ids, the validIds gate that makes ChatMessage's @html
// safe - so everything read back passes through the whitelist below, the way
// lib/highlights.ts shape-filters its own storage.
//
// Streamed messages are created with $state and the proxy itself is retained - the exact
// semantics pinned in chat-state.svelte.test.ts; a plain object mutated through a retained
// raw reference is invisible to the template.

import type { FastResult, KeywordCount, Message, UsedPaper, WebHit } from './chat-types';
import { loaderMatches } from './keyword-match';
import { parseSseFrame, splitSseBuffer, type SseEvent } from './sse';

// Module-scope state in a module that IS evaluated during SSR (Base.astro renders
// ScoutPanel server-side), where it is shared by every request in the Node process.
// Cross-request isolation rests on the window guard around the restore call at the bottom
// of this file: nothing at module scope above it may touch storage, the DOM, or push into
// this state on the server.
export const chat = $state({
  messages: [] as Message[],
  busy: false,
  // Whether a conversation exists (asked this visit, or restored). The omnibox shows the
  // thread instead of the welcome block once this is set.
  asked: false,
});

// The "ask about this paper" pin: questions go retrieval-scoped to one paper until the
// reader clears the chip. Deliberately not persisted - a pin is a moment's intent, and
// restoring it silently would make tomorrow's unrelated question answer about old context.
export const scope = $state({
  paperId: null as string | null,
  title: null as string | null,
});

export function setScope(paperId: string, title: string): void {
  scope.paperId = paperId;
  scope.title = title;
}

export function clearScope(): void {
  scope.paperId = null;
  scope.title = null;
}

// How much conversation the backend sees; mirrors the API's max_length on history.
const HISTORY_TURNS = 6;
const HISTORY_TURN_CHARS = 2000;

function buildHistory(): { role: 'user' | 'assistant'; text: string }[] {
  // Snapshot BEFORE the new question is pushed. Only turns with text survive: a card-only
  // fast answer still carries its rendered text, but an errored or empty turn says nothing.
  return chat.messages
    .filter((message) => message.text.trim().length > 0 && !message.error)
    .slice(-HISTORY_TURNS)
    .map((message) => ({
      role: message.role,
      text: message.text.slice(0, HISTORY_TURN_CHARS),
    }));
}

let controller: AbortController | null = null;

const STORAGE_KEY = 'rs-scout-chat';
// v2 added the owner tag; bumping discarded every pre-owner transcript once, which is the
// safe direction on a shared browser.
const VERSION = 2;
const MAX_AGE_MS = 24 * 60 * 60 * 1000;
const MAX_MESSAGES = 40;

// The account tag Base.astro stamps on <html> (an opaque hash, never the sub). Signed-out
// visitors share the empty tag - a deliberate limit: without an account there is no identity
// to scope to, exactly like the theme preference.
function currentOwner(): string {
  if (typeof document === 'undefined') return '';
  return document.documentElement.dataset.owner ?? '';
}

// Canonical paper ids ("arxiv:2401.12345", "doi:10.1000/x"). Anything restored that will be
// linkified or used as a citation gate must match; a stored id that does not is dropped.
const ID_SHAPE = /^[a-z0-9]+:[A-Za-z0-9._/-]{1,80}$/;
const ARXIV_SHAPE = /^[A-Za-z0-9./-]{1,40}$/;

function trimThread(): void {
  // The cap must hold for the live array too, not just the persisted copy: the omnibox
  // never unmounts and the module survives soft navigation, so an untrimmed thread grows
  // for the life of the tab. Splicing from the front keeps every retained message proxy
  // valid - a streaming `current` is always at the tail.
  if (chat.messages.length > MAX_MESSAGES) {
    chat.messages.splice(0, chat.messages.length - MAX_MESSAGES);
  }
}

export function stopStreaming(): void {
  controller?.abort();
}

export function clearConversation(): void {
  stopStreaming();
  chat.messages.length = 0;
  chat.asked = false;
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage being unavailable only means there was nothing to clear.
  }
}

function applyEvent(frame: SseEvent, current: Message) {
  const payload = frame.payload as Record<string, any>;
  if (frame.event === 'meta') {
    current.retrieved = payload.retrieved;
    if (current.phase !== 'streaming') current.phase = 'thinking';
  } else if (frame.event === 'results') {
    current.results = payload.items;
  } else if (frame.event === 'notfound') {
    current.notfound = { query: payload.query, webSearch: Boolean(payload.web_search) };
    current.text = 'No papers in your library matched this question.';
  } else if (frame.event === 'token') {
    // The first token flips to streaming whether or not meta arrived; guardrail refusals
    // skip meta entirely. Fast messages with structured results render cards, so the
    // duplicate raw text never accumulates.
    current.phase = 'streaming';
    if (!current.results) current.text += payload.delta;
  } else if (frame.event === 'done') {
    current.phase = 'done';
    current.cited = payload.cited;
    current.used = payload.used;
  } else if (frame.event === 'error') {
    current.phase = 'done';
    current.text = payload.message ?? 'Something went wrong.';
    current.error = true;
  }
  schedulePersist();
}

export async function ask(
  question: string,
  mode: 'fast' | 'llm',
  dictionary: KeywordCount[] | null = null,
  options: { deep?: boolean } = {},
): Promise<void> {
  if (chat.busy) return;
  // History snapshots before the new turns join the thread; fast mode sends none (the
  // backend ignores it there by design, so the bytes would say nothing).
  const history = mode === 'llm' ? buildHistory() : [];
  const label = options.deep ? `/deep ${question}` : mode === 'llm' ? `/ai ${question}` : question;
  chat.messages.push({ role: 'user', text: label });
  chat.asked = true;
  chat.busy = true;
  controller = new AbortController();
  const current: Message = $state({
    role: 'assistant',
    text: '',
    phase: 'searching',
    mode,
    question,
  });
  if (mode === 'fast' && dictionary) {
    // The loader line names the dictionary keywords the question hit.
    current.matched = loaderMatches(question, dictionary);
  }
  chat.messages.push(current);
  trimThread();

  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        question,
        mode,
        ...(options.deep ? { agentic: true } : {}),
        ...(history.length > 0 ? { history } : {}),
        ...(scope.paperId ? { paper_id: scope.paperId } : {}),
      }),
      signal: controller.signal,
    });
    if (response.status === 401) {
      // Generated answers need an account; the quick ones do not. Say which, and offer
      // the way in, rather than reporting this as a failure.
      current.text = 'Generated answers need an account.';
      current.needsSignIn = true;
      return;
    }
    if (response.status === 429) {
      const wait = response.headers.get('Retry-After');
      current.text = `Slow down a little - try again in ${wait ?? 'a few'} seconds.`;
      current.error = true;
      return;
    }
    if (!response.ok || !response.body) {
      current.text = 'The research service is unavailable right now.';
      current.error = true;
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { frames, rest } = splitSseBuffer(buffer);
      buffer = rest;
      for (const piece of frames) {
        const parsed = parseSseFrame(piece);
        if (parsed) applyEvent(parsed, current);
      }
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      // The Stop button: keep whatever streamed and mark the message, no error styling.
      current.stopped = true;
    } else {
      current.text = 'Connection lost mid-answer - try again.';
      current.error = true;
    }
  } finally {
    current.phase = 'done';
    chat.busy = false;
    controller = null;
    schedulePersist();
  }
}

export async function runWebSearch(query: string): Promise<void> {
  // The /web command: an assistant message holding web hit cards, reusing the same
  // fallback rendering and one-click import flow as the notfound path.
  if (chat.busy) return;
  chat.messages.push({ role: 'user', text: `/web ${query}` });
  chat.asked = true;
  chat.busy = true;
  controller = new AbortController();
  const current: Message = $state({
    role: 'assistant',
    text: '',
    phase: 'searching',
    question: query,
    webBusy: true,
  });
  chat.messages.push(current);
  trimThread();
  try {
    const response = await fetch(`/api/search/web?q=${encodeURIComponent(query)}`, {
      signal: controller.signal,
    });
    if (response.status === 401) {
      current.text = 'Searching the web needs an account.';
      current.needsSignIn = true;
      return;
    }
    if (response.status === 404) {
      current.text = 'Web search is disabled.';
      current.error = true;
      return;
    }
    if (response.status === 429) {
      current.text = 'Slow down a little - try again in a few seconds.';
      current.error = true;
      return;
    }
    if (!response.ok) throw new Error(String(response.status));
    const payload = await response.json();
    current.webHits = payload.hits;
    current.webFailed = payload.providers_failed;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      current.stopped = true;
    } else {
      current.webHits = [];
      current.webFailed = ['arxiv', 's2'];
    }
  } finally {
    current.webBusy = false;
    current.phase = 'done';
    chat.busy = false;
    controller = null;
    schedulePersist();
  }
}

export function summarize(message: Message): void {
  // The on-demand LLM pass over the same question, as a fresh exchange.
  if (chat.busy || !message.question) return;
  void ask(message.question, 'llm');
}

export async function searchWeb(message: Message): Promise<void> {
  const query = message.notfound?.query;
  if (!query || message.webBusy) return;
  message.webBusy = true;
  try {
    const response = await fetch(`/api/search/web?q=${encodeURIComponent(query)}`);
    if (response.status === 401) {
      message.needsSignIn = true;
      return;
    }
    if (!response.ok) throw new Error(String(response.status));
    const payload = await response.json();
    message.webHits = payload.hits;
    message.webFailed = payload.providers_failed;
  } catch {
    message.webHits = [];
    message.webFailed = ['arxiv', 's2'];
  } finally {
    message.webBusy = false;
    schedulePersist();
  }
}

export async function importHit(message: Message, hit: WebHit): Promise<void> {
  if (!hit.arxiv_id) return;
  message.imports = { ...(message.imports ?? {}), [hit.arxiv_id]: 'busy' };
  try {
    const response = await fetch('/api/papers/import', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ arxiv_id: hit.arxiv_id }),
    });
    if (!response.ok) throw new Error(String(response.status));
    const payload = await response.json();
    message.imports = { ...message.imports, [hit.arxiv_id]: payload.id };
  } catch {
    message.imports = { ...message.imports, [hit.arxiv_id]: 'error' };
  } finally {
    schedulePersist();
  }
}

// --- persistence -------------------------------------------------------------------------

let persistTimer: ReturnType<typeof setTimeout> | null = null;

function schedulePersist(): void {
  if (typeof window === 'undefined') return;
  if (persistTimer) clearTimeout(persistTimer);
  persistTimer = setTimeout(persistNow, 400);
}

export function persistNow(): void {
  if (typeof localStorage === 'undefined') return;
  if (persistTimer) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  try {
    if (chat.messages.length === 0) {
      localStorage.removeItem(STORAGE_KEY);
      return;
    }
    const messages = chat.messages.slice(-MAX_MESSAGES).map(serializeMessage);
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ v: VERSION, owner: currentOwner(), savedAt: Date.now(), messages }),
    );
  } catch {
    // Quota or private mode: the conversation simply does not survive the reload.
  }
}

function serializeMessage(message: Message): Record<string, unknown> {
  // An explicit field list rather than the object itself: `matched` is a transient loader
  // line, and anything not written here cannot come back to surprise the restore.
  return {
    role: message.role,
    text: message.text,
    mode: message.mode,
    question: message.question,
    cited: message.cited,
    used: message.used,
    error: message.error,
    needsSignIn: message.needsSignIn,
    stopped: message.stopped,
    results: message.results,
    notfound: message.notfound,
    webHits: message.webHits,
    webFailed: message.webFailed,
    imports: message.imports,
  };
}

/** Restore yesterday's conversation, discarding anything expired or malformed. */
export function restoreConversation(): void {
  if (typeof localStorage === 'undefined') return;
  let parsed: unknown;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    parsed = JSON.parse(raw);
  } catch {
    return;
  }
  if (typeof parsed !== 'object' || parsed === null) return;
  const envelope = parsed as { v?: unknown; owner?: unknown; savedAt?: unknown; messages?: unknown };
  if (envelope.v !== VERSION) return;
  if ((envelope.owner ?? '') !== currentOwner()) {
    // Another account's conversation on a shared browser: remove it rather than merely
    // skipping it, so it does not linger for whoever signs in next.
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // Removal is best-effort; a throwing storage already failed the read path.
    }
    return;
  }
  if (typeof envelope.savedAt !== 'number' || Date.now() - envelope.savedAt > MAX_AGE_MS) return;
  if (!Array.isArray(envelope.messages)) return;
  const cleaned = envelope.messages
    .slice(-MAX_MESSAGES)
    .map(cleanMessage)
    .filter((message): message is Message => message !== null);
  if (cleaned.length === 0) return;
  // Plain pushes are fine here: no raw reference is retained, so every later read and
  // mutation goes through the array's proxy.
  chat.messages.push(...cleaned);
  trimThread();
  chat.asked = true;
}

// --- restore whitelist -------------------------------------------------------------------

function str(value: unknown, cap: number): string | null {
  return typeof value === 'string' && value.length <= cap ? value : null;
}

function strList(value: unknown, cap: number, itemCap: number): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string' && item.length <= itemCap).slice(0, cap);
}

function cleanUsed(value: unknown): UsedPaper[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const cleaned: UsedPaper[] = [];
  for (const item of value.slice(0, 50)) {
    if (typeof item !== 'object' || item === null) continue;
    const candidate = item as Record<string, unknown>;
    const id = str(candidate.id, 100);
    const title = str(candidate.title, 500);
    // The id gates the @html citation linkifier, so its shape is the security boundary.
    if (!id || !ID_SHAPE.test(id) || title === null) continue;
    cleaned.push({ id, title, score: typeof candidate.score === 'number' ? candidate.score : 0 });
  }
  return cleaned.length > 0 ? cleaned : undefined;
}

function cleanResults(value: unknown): FastResult[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const cleaned: FastResult[] = [];
  for (const item of value.slice(0, 20)) {
    if (typeof item !== 'object' || item === null) continue;
    const candidate = item as Record<string, unknown>;
    const id = str(candidate.id, 100);
    const title = str(candidate.title, 500);
    if (!id || !ID_SHAPE.test(id) || title === null) continue;
    cleaned.push({
      id,
      title,
      published_at: str(candidate.published_at, 40) ?? '',
      venue: str(candidate.venue, 200),
      matches: strList(candidate.matches, 12, 80),
      keywords: strList(candidate.keywords, 12, 80),
      excerpt: str(candidate.excerpt, 1200),
      relevance: typeof candidate.relevance === 'number' ? candidate.relevance : null,
    });
  }
  return cleaned.length > 0 ? cleaned : undefined;
}

function cleanWebHits(value: unknown): WebHit[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const cleaned: WebHit[] = [];
  for (const item of value.slice(0, 20)) {
    if (typeof item !== 'object' || item === null) continue;
    const candidate = item as Record<string, unknown>;
    const title = str(candidate.title, 500);
    if (title === null) continue;
    const arxivId = str(candidate.arxiv_id, 40);
    const paperId = str(candidate.paper_id, 100);
    cleaned.push({
      provider: str(candidate.provider, 40) ?? '',
      title,
      authors: strList(candidate.authors, 12, 200),
      year: typeof candidate.year === 'number' ? candidate.year : null,
      snippet: str(candidate.snippet, 1000) ?? '',
      arxiv_id: arxivId && ARXIV_SHAPE.test(arxivId) ? arxivId : null,
      url: cleanUrl(candidate.url),
      already_known: candidate.already_known === true,
      paper_id: paperId && ID_SHAPE.test(paperId) ? paperId : null,
    });
  }
  return cleaned.length > 0 ? cleaned : undefined;
}

function cleanUrl(value: unknown): string | null {
  // These become hrefs; only web URLs come back, never javascript: or data:.
  const candidate = str(value, 500);
  if (!candidate) return null;
  try {
    const url = new URL(candidate);
    return url.protocol === 'https:' || url.protocol === 'http:' ? candidate : null;
  } catch {
    return null;
  }
}

function cleanImports(value: unknown): Record<string, string> | undefined {
  if (typeof value !== 'object' || value === null) return undefined;
  const cleaned: Record<string, string> = {};
  for (const [key, raw] of Object.entries(value as Record<string, unknown>).slice(0, 20)) {
    if (!ARXIV_SHAPE.test(key) || typeof raw !== 'string') continue;
    // An import that was mid-flight when the page went away did not finish.
    cleaned[key] = raw === 'busy' ? 'error' : ID_SHAPE.test(raw) ? raw : 'error';
  }
  return Object.keys(cleaned).length > 0 ? cleaned : undefined;
}

function cleanMessage(value: unknown): Message | null {
  if (typeof value !== 'object' || value === null) return null;
  const candidate = value as Record<string, unknown>;
  if (candidate.role !== 'user' && candidate.role !== 'assistant') return null;
  const text = str(candidate.text, 20_000) ?? '';
  const notfoundRaw =
    typeof candidate.notfound === 'object' && candidate.notfound !== null
      ? (candidate.notfound as Record<string, unknown>)
      : null;
  const notfoundQuery = notfoundRaw ? str(notfoundRaw.query, 500) : null;
  const message: Message = {
    role: candidate.role,
    text,
    // Whatever phase was stored, the stream behind it is gone: everything restores done,
    // or the loader would spin forever.
    phase: candidate.role === 'assistant' ? 'done' : undefined,
    mode: candidate.mode === 'fast' || candidate.mode === 'llm' ? candidate.mode : undefined,
    question: str(candidate.question, 2000) ?? undefined,
    cited: strList(candidate.cited, 50, 100).filter((id) => ID_SHAPE.test(id)),
    used: cleanUsed(candidate.used),
    error: candidate.error === true || undefined,
    needsSignIn: candidate.needsSignIn === true || undefined,
    stopped: candidate.stopped === true || undefined,
    results: cleanResults(candidate.results),
    notfound: notfoundQuery ? { query: notfoundQuery, webSearch: notfoundRaw?.webSearch === true } : undefined,
    webBusy: false,
    webHits: cleanWebHits(candidate.webHits),
    webFailed: strList(candidate.webFailed, 5, 20),
    imports: cleanImports(candidate.imports),
  };
  if (message.cited?.length === 0) message.cited = undefined;
  if (message.webFailed?.length === 0) message.webFailed = undefined;
  return message;
}

if (typeof window !== 'undefined') {
  restoreConversation();
  // The debounce above means the newest frames may only be in memory when the tab goes
  // away; pagehide is the last reliable moment to write them.
  window.addEventListener('pagehide', persistNow);
}
