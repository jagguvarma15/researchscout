// Pattern-match user prompts against the corpus keyword dictionary (GET /v1/keywords).

import type { KeywordCount } from './chat-types';

const TOKEN = /[a-z0-9]+/g;

function tokens(text: string): string[] {
  return text.toLowerCase().match(TOKEN) ?? [];
}

function ranked(a: KeywordCount, b: KeywordCount): number {
  return b.papers - a.papers || a.keyword.localeCompare(b.keyword);
}

// Typeahead: rank dictionary keywords against the partial prompt. The last token acts as
// a prefix (the word still being typed); earlier tokens match as substrings. Keywords the
// input already contains verbatim are excluded - suggesting them again is noise.
export function matchKeywords(input: string, dictionary: KeywordCount[], cap = 6): KeywordCount[] {
  const parts = tokens(input);
  const last = parts[parts.length - 1];
  if (!last) return [];
  const lowered = input.toLowerCase();
  return dictionary
    .filter(({ keyword }) => {
      if (lowered.includes(keyword)) return false;
      if (last.length >= 2 && keyword.split(' ').some((word) => word.startsWith(last))) {
        return true;
      }
      return parts.some((token) => token.length >= 3 && keyword.includes(token));
    })
    .sort(ranked)
    .slice(0, cap);
}

// Post-send loader line: which dictionary keywords does the question hit, and roughly how
// many papers carry them. A keyword matches when the question contains it verbatim or
// every one of its words appears as a question token. papers sums per-keyword counts:
// per-keyword totals cannot give a distinct-paper count client-side, and for a transient
// status line the overlap approximation is fine.
export function loaderMatches(
  question: string,
  dictionary: KeywordCount[],
  cap = 3,
): { keywords: string[]; papers: number } | null {
  const lowered = question.toLowerCase();
  const parts = new Set(tokens(question));
  const hits = dictionary
    .filter(({ keyword }) => {
      if (lowered.includes(keyword)) return true;
      const words = keyword.split(' ');
      return words.length > 0 && words.every((word) => parts.has(word));
    })
    .sort(ranked)
    .slice(0, cap);
  if (hits.length === 0) return null;
  return {
    keywords: hits.map((hit) => hit.keyword),
    papers: hits.reduce((sum, hit) => sum + hit.papers, 0),
  };
}
