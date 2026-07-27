// Server-side LaTeX rendering for paper titles and abstracts. Follows renderDigestBody's
// escape-then-enrich contract: split the raw string into text and math segments first, escape
// every text segment, render math segments with KaTeX, and fall back to the escaped source
// when the TeX is broken — never re-parse produced HTML.

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
