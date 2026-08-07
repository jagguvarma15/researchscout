import { describe, expect, it } from 'vitest';

import { formatMonthYear, renderAnswerHtml } from './chat-format';

const IDS = new Set(['arxiv:2401.00001', 'arxiv:2401.00002']);

describe('renderAnswerHtml', () => {
  it('escapes HTML before any enrichment', () => {
    expect(renderAnswerHtml('<script>alert(1)</script>', IDS)).toBe(
      '<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>',
    );
  });

  it('does not double-escape entities', () => {
    expect(renderAnswerHtml('AT&T', IDS)).toBe('<p>AT&amp;T</p>');
  });

  it('linkifies only server-confirmed citation ids', () => {
    const html = renderAnswerHtml('Real [arxiv:2401.00001] fake [arxiv:9999.99999]', IDS);
    expect(html).toContain('<a href="/papers/arxiv:2401.00001">[arxiv:2401.00001]</a>');
    expect(html).toContain('fake [arxiv:9999.99999]');
    expect(html).not.toContain('/papers/arxiv:9999.99999');
  });

  it('never linkifies an id with an attribute-breaking quote', () => {
    const html = renderAnswerHtml('bad [arxiv:x"onmouseover=x]', IDS);
    expect(html).not.toContain('<a');
    expect(html).toContain('[arxiv:x&quot;onmouseover=x]'.replace('&quot;', '"'));
  });

  it('renders bold and italic without touching escaped angle brackets', () => {
    expect(renderAnswerHtml('**a<b** and *c*', IDS)).toBe(
      '<p><strong>a&lt;b</strong> and <em>c</em></p>',
    );
  });

  it('keeps markdown and citations literal inside code spans', () => {
    const html = renderAnswerHtml('run `x = *y* [arxiv:2401.00001]` now', IDS);
    expect(html).toBe('<p>run <code>x = *y* [arxiv:2401.00001]</code> now</p>');
  });

  it('linkifies a citation inside bold', () => {
    const html = renderAnswerHtml('**see [arxiv:2401.00002]**', IDS);
    expect(html).toBe(
      '<p><strong>see <a href="/papers/arxiv:2401.00002">[arxiv:2401.00002]</a></strong></p>',
    );
  });

  it('keeps markdown links as escaped literal text', () => {
    const html = renderAnswerHtml('[click](javascript:alert(1))', IDS);
    expect(html).not.toContain('<a');
    expect(html).toContain('[click](javascript:alert(1))');
  });

  it('splits paragraphs on blank lines and keeps line breaks', () => {
    expect(renderAnswerHtml('a\nb\n\nc', IDS)).toBe('<p>a<br />b</p><p>c</p>');
  });

  it('does not treat plain digits as code placeholders', () => {
    expect(renderAnswerHtml('chapter 3 of `x` is out', IDS)).toBe(
      '<p>chapter 3 of <code>x</code> is out</p>',
    );
  });

  it('renders dash and star runs as one unordered list', () => {
    expect(renderAnswerHtml('intro\n- first\n* second\nafter', IDS)).toBe(
      '<p>intro</p><ul><li>first</li><li>second</li></ul><p>after</p>',
    );
  });

  it('renders numbered runs as an ordered list', () => {
    expect(renderAnswerHtml('1. one\n2. two', IDS)).toBe('<ol><li>one</li><li>two</li></ol>');
  });

  it('applies emphasis and citations inside list items', () => {
    expect(renderAnswerHtml('- **bold** [arxiv:2401.00001]', IDS)).toBe(
      '<ul><li><strong>bold</strong> <a href="/papers/arxiv:2401.00001">[arxiv:2401.00001]</a></li></ul>',
    );
  });

  it('keeps a whole-line italic out of the bullet parser', () => {
    // No space after the asterisk: emphasis, not a list marker.
    expect(renderAnswerHtml('*aside*', IDS)).toBe('<p><em>aside</em></p>');
  });

  it('flattens heading levels to h4', () => {
    expect(renderAnswerHtml('## Findings\ntext', IDS)).toBe('<h4>Findings</h4><p>text</p>');
    expect(renderAnswerHtml('### Sub', IDS)).toBe('<h4>Sub</h4>');
  });

  it('renders fenced blocks with their content escaped and unenriched', () => {
    const html = renderAnswerHtml('```python\nx = "<b>" # **not bold**\n```', IDS);
    expect(html).toBe('<pre><code>x = "&lt;b&gt;" # **not bold**</code></pre>');
    expect(html).not.toContain('<strong>');
  });

  it('keeps citations and list markers literal inside fences', () => {
    const html = renderAnswerHtml('```\n- [arxiv:2401.00001]\n```', IDS);
    expect(html).toBe('<pre><code>- [arxiv:2401.00001]</code></pre>');
    expect(html).not.toContain('<a');
    expect(html).not.toContain('<ul>');
  });

  it('escapes injection attempts inside every new block type', () => {
    expect(renderAnswerHtml('- <img src=x onerror=alert(1)>', IDS)).toBe(
      '<ul><li>&lt;img src=x onerror=alert(1)&gt;</li></ul>',
    );
    expect(renderAnswerHtml('## <script>x</script>', IDS)).toBe(
      '<h4>&lt;script&gt;x&lt;/script&gt;</h4>',
    );
  });
});

describe('formatMonthYear', () => {
  it('renders the fixed English month and year', () => {
    expect(formatMonthYear('2026-07-01T00:00:00Z')).toBe('Jul 2026');
    expect(formatMonthYear('2024-01-31T23:59:59+00:00')).toBe('Jan 2024');
  });

  it('falls back to the year on a malformed month', () => {
    expect(formatMonthYear('2026-99-01')).toBe('2026');
  });
});
