// Render LLM answer text to safe HTML. Follows renderDigestBody's escape-then-enrich
// contract (lib/api.ts): escape everything first, then introduce tags only from a fixed
// literal set, and never re-parse produced HTML.
//
// Supported inline markdown, matching what the local models actually emit: **bold**,
// *italic*, and single-line `code` spans. Headings, lists, and markdown links are NOT
// supported - a raw [text](url) stays escaped literal text, which closes the URL
// injection door. Citations [scheme:id] become paper links ONLY when the id is in
// validIds (the server-confirmed used set). That gate is load-bearing for safety: the
// citation regex alone would pass a quote character through into the href, so ids the
// model invented are never linkified.

const CITATION = /\[([a-z]+:[^\]\s]+)\]/g;
const CODE_SPAN = /`([^`\n]+)`/g;
const BOLD = /\*\*([^*\n]+)\*\*/g;
const ITALIC = /\*([^*\n]+)\*/g;
const HOLE = /\u0000(\d+)\u0000/g;

export function renderAnswerHtml(text: string, validIds: Set<string>): string {
  const escaped = text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
  // Park code spans in NUL-delimited placeholders so their content is exempt from
  // linkification and emphasis; real input never contains NUL.
  const codes: string[] = [];
  const holed = escaped.replace(CODE_SPAN, (_, code: string) => {
    codes.push(code);
    return `\u0000${codes.length - 1}\u0000`;
  });
  const linked = holed.replace(CITATION, (match, id: string) =>
    validIds.has(id) ? `<a href="/papers/${id}">[${id}]</a>` : match,
  );
  const emphasized = linked.replace(BOLD, '<strong>$1</strong>').replace(ITALIC, '<em>$1</em>');
  const restored = emphasized.replace(HOLE, (_, index: string) => `<code>${codes[Number(index)]}</code>`);
  return restored
    .split(/\n{2,}/)
    .map((block) => `<p>${block.replaceAll('\n', '<br />')}</p>`)
    .join('');
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// "2026-07-01T00:00:00Z" -> "Jul 2026". String slicing, not Date: no timezone drift,
// and fixed English names match the backend's %b %Y rendering.
export function formatMonthYear(iso: string): string {
  const year = iso.slice(0, 4);
  const month = MONTHS[Number(iso.slice(5, 7)) - 1];
  return month ? `${month} ${year}` : year;
}
