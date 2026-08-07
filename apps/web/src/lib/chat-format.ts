// Render LLM answer text to safe HTML. Follows renderDigestBody's escape-then-enrich
// contract (lib/api.ts): escape everything first, then introduce tags only from a fixed
// literal set, and never re-parse produced HTML.
//
// Supported markdown, matching what the local models actually emit: **bold**, *italic*,
// single-line `code` spans, ``` fenced blocks, `- `/`* `/`1. ` lists, and `##` headings
// (every level flattens to h4 - the panel has one heading size). Markdown links are NOT
// supported - a raw [text](url) stays escaped literal text, which closes the URL
// injection door. Citations [scheme:id] become paper links ONLY when the id is in
// validIds (the server-confirmed used set). That gate is load-bearing for safety: the
// citation regex alone would pass a quote character through into the href, so ids the
// model invented are never linkified.
//
// Order matters: fences and code spans are parked first (their content gets no inline
// processing), the block pass then works on structure, and inline enrichment runs on each
// text fragment AFTER its list marker is stripped - running italics over a whole `* item`
// line would eat the bullet asterisk as an emphasis delimiter.

const CITATION = /\[([a-z]+:[^\]\s]+)\]/g;
const CODE_SPAN = /`([^`\n]+)`/g;
const BOLD = /\*\*([^*\n]+)\*\*/g;
const ITALIC = /\*([^*\n]+)\*/g;
const FENCE = /```[^\n]*\n([\s\S]*?)```/g;
const HOLE = /\u0000(\d+)\u0000/g;
const FENCE_HOLE = /^\u0000F(\d+)\u0000$/;
const BULLET = /^[-*]\s+(.*)$/;
const ORDERED = /^\d+[.)]\s+(.*)$/;
const HEADING = /^#{2,4}\s+(.*)$/;

export function renderAnswerHtml(text: string, validIds: Set<string>): string {
  const escaped = text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
  // Park fenced blocks, then code spans, in NUL-delimited placeholders so their content is
  // exempt from linkification, emphasis, and list detection; real input never contains NUL.
  const fences: string[] = [];
  const fenced = escaped.replace(FENCE, (_, body: string) => {
    fences.push(body.replace(/\n$/, ''));
    return `\n\u0000F${fences.length - 1}\u0000\n`;
  });
  const codes: string[] = [];
  const holed = fenced.replace(CODE_SPAN, (_, code: string) => {
    codes.push(code);
    return `\u0000${codes.length - 1}\u0000`;
  });

  const inline = (fragment: string): string =>
    fragment
      .replace(CITATION, (match, id: string) =>
        validIds.has(id) ? `<a href="/papers/${id}">[${id}]</a>` : match,
      )
      .replace(BOLD, '<strong>$1</strong>')
      .replace(ITALIC, '<em>$1</em>')
      .replace(HOLE, (_, index: string) => `<code>${codes[Number(index)]}</code>`);

  const out: string[] = [];
  let para: string[] = [];
  let list: { tag: 'ul' | 'ol'; items: string[] } | null = null;
  const flushPara = () => {
    if (para.length) out.push(`<p>${para.join('<br />')}</p>`);
    para = [];
  };
  const flushList = () => {
    if (list) {
      out.push(
        `<${list.tag}>${list.items.map((item) => `<li>${item}</li>`).join('')}</${list.tag}>`,
      );
    }
    list = null;
  };

  for (const raw of holed.split('\n')) {
    const line = raw.trim();
    const fence = FENCE_HOLE.exec(line);
    const bullet = BULLET.exec(line);
    const ordered = ORDERED.exec(line);
    const heading = HEADING.exec(line);
    if (!line) {
      flushPara();
      flushList();
    } else if (fence) {
      flushPara();
      flushList();
      out.push(`<pre><code>${fences[Number(fence[1])]}</code></pre>`);
    } else if (bullet) {
      flushPara();
      if (list?.tag !== 'ul') flushList();
      list ??= { tag: 'ul', items: [] };
      list.items.push(inline(bullet[1]));
    } else if (ordered) {
      flushPara();
      if (list?.tag !== 'ol') flushList();
      list ??= { tag: 'ol', items: [] };
      list.items.push(inline(ordered[1]));
    } else if (heading) {
      flushPara();
      flushList();
      out.push(`<h4>${inline(heading[1])}</h4>`);
    } else {
      flushList();
      para.push(inline(line));
    }
  }
  flushPara();
  flushList();
  return out.join('');
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// "2026-07-01T00:00:00Z" -> "Jul 2026". String slicing, not Date: no timezone drift,
// and fixed English names match the backend's %b %Y rendering.
export function formatMonthYear(iso: string): string {
  const year = iso.slice(0, 4);
  const month = MONTHS[Number(iso.slice(5, 7)) - 1];
  return month ? `${month} ${year}` : year;
}
