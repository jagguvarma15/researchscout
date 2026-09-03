// Server-side LaTeX rendering for paper titles, abstracts, and issue prose. All of it follows
// the escape-then-enrich contract: split the raw string into text and special segments first,
// escape every text segment, render math segments with KaTeX, and fall back to the escaped
// source when the TeX is broken — never re-parse produced HTML.

import katex from 'katex';

import { MATH_SPAN, stripMath } from './math-text';

export { stripMath };

function escapeHtml(text: string): string {
  return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

export function renderMathHTML(text: string): string {
  let out = '';
  let last = 0;
  for (const match of text.matchAll(MATH_SPAN)) {
    const index = match.index ?? 0;
    out += escapeHtml(text.slice(last, index));
    try {
      out += katex.renderToString(match[1], { throwOnError: true, output: 'html' });
    } catch {
      out += escapeHtml(match[0]); // broken TeX stays visible as escaped source
    }
    last = index + match[0].length;
  }
  out += escapeHtml(text.slice(last));
  return out;
}

const CITATION = /\[([a-z]+:[^\]\s]+)\]/g;

// Digest bodies are plain LLM (or fallback) text: [scheme:id] citations become paper links,
// blank lines become paragraph breaks, and everything else renders through renderMathHTML so
// inline TeX in the prose displays. Only ids actually in the issue get linked — an id the
// model invented stays as escaped plain text instead of a dead link.
export function renderIssueBody(text: string, validIds: Set<string>): string {
  const fragment = (piece: string): string => {
    let out = '';
    let last = 0;
    for (const match of piece.matchAll(CITATION)) {
      const index = match.index ?? 0;
      out += renderMathHTML(piece.slice(last, index));
      const id = match[1];
      out += validIds.has(id) ? `<a href="/papers/${id}">[${id}]</a>` : escapeHtml(match[0]);
      last = index + match[0].length;
    }
    out += renderMathHTML(piece.slice(last));
    return out;
  };
  return text
    .split(/\n{2,}/)
    .map((block) => `<p>${block.split('\n').map(fragment).join('<br />')}</p>`)
    .join('');
}
