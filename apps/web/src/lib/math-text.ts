// Pure text-side math helpers, safe for client bundles (no KaTeX import).

// $...$ with a non-space character just inside both delimiters and no $ in between —
// matches arXiv-style inline math while dodging prose like "costs $5 and $10".
export const MATH_SPAN = /\$([^\s$][^$]*?[^\s$]|[^\s$])\$/g;

// Plain-text fallback for contexts that cannot take HTML (palette labels, document titles):
// the delimiters go, the TeX source stays readable.
export function stripMath(text: string): string {
  return text.replace(MATH_SPAN, (_match, tex: string) => tex);
}
