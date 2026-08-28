// What the omnibox does with what you typed.
//
// One field now serves two jobs that used to be two surfaces: finding a paper, and asking
// Scout a question about the corpus. Nothing here decides irreversibly - the panel always
// lists matching papers as you type AND always offers the ask row, so this only chooses which
// of the two is highlighted first, and being wrong costs one arrow key.
//
// That is why the rules are narrow. A trailing question mark or an opening question word are
// the two signals that mean a question in every case; a word count is not. "sparse mixture of
// experts routing for multilingual translation" is eight words and plainly a lookup, so
// counting words would misfire on exactly the long technical phrases this corpus is full of.

import { parseInput } from './commands';

export type Intent = 'lookup' | 'question' | 'command';

// Words that only ever open a question. Deliberately excludes ones that also open a noun
// phrase - "will" and "can" appear mid-title often enough, but never in first position.
const QUESTION_OPENERS = new Set([
  'am',
  'are',
  'can',
  'compare',
  'could',
  'did',
  'do',
  'does',
  'explain',
  'has',
  'have',
  'how',
  'is',
  'should',
  'summarise',
  'summarize',
  'was',
  'were',
  'what',
  'when',
  'where',
  'which',
  'who',
  'whose',
  'why',
  'will',
  'would',
]);

/** Whether the highlighted default should be asking Scout rather than opening a paper. */
export function classify(raw: string): Intent {
  const trimmed = raw.trim();
  if (!trimmed) return 'lookup';
  if (trimmed.startsWith('/')) return 'command';
  if (trimmed.endsWith('?')) return 'question';
  const first = trimmed.split(/\s+/, 1)[0].toLowerCase().replace(/[^a-z]/g, '');
  return QUESTION_OPENERS.has(first) ? 'question' : 'lookup';
}

/** True when the input is a slash command the composer understands (not an unknown one). */
export function isKnownCommand(raw: string): boolean {
  const parsed = parseInput(raw);
  return parsed.kind === 'web' || parsed.kind === 'ai' || parsed.kind === 'deep';
}

/** The results URL for a full search, which is where "search all papers" hands off to. */
export function searchUrl(query: string): string {
  const trimmed = query.trim();
  return trimmed ? `/search?q=${encodeURIComponent(trimmed)}` : '/';
}

/**
 * Keeps the newest reply and discards the rest.
 *
 * Typeahead fires a request per keystroke and they finish out of order, so a slow reply to
 * "tran" can land after a fast reply to "transformer" and overwrite it with worse results.
 * Take a ticket before the request; check it still holds before using the answer.
 */
export function createSequencer(): { next: () => number; isCurrent: (seq: number) => boolean } {
  let latest = 0;
  return {
    next: () => ++latest,
    isCurrent: (seq: number) => seq === latest,
  };
}

/** A trailing-edge debounce with a cancel, so an unmount does not fire one last request. */
export function debounce(fn: () => void, ms: number): { run: () => void; cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return {
    run() {
      clearTimeout(timer);
      timer = setTimeout(fn, ms);
    },
    cancel() {
      clearTimeout(timer);
      timer = undefined;
    },
  };
}
