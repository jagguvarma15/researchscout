// @vitest-environment node
//
// The issue-body renderer's escape-then-enrich discipline, carried over from the old
// renderDigestBody tests and extended with the math path: citations link only when the issue
// actually contains them, markup is escaped before anything else, and inline TeX renders.

import { describe, expect, it } from 'vitest';

import { renderIssueBody, renderMathHTML } from './math';

describe('renderMathHTML', () => {
  it('escapes text and renders inline math', () => {
    const html = renderMathHTML('a <b> and $x^2$');
    expect(html).toContain('a &lt;b&gt; and ');
    expect(html).toContain('katex');
  });
});

describe('renderIssueBody', () => {
  it('escapes markup before anything else', () => {
    const html = renderIssueBody('a <script> & b', new Set());
    expect(html).toBe('<p>a &lt;script&gt; &amp; b</p>');
  });

  it('links only citations the issue actually contains', () => {
    const html = renderIssueBody('[arxiv:1] and [arxiv:2]', new Set(['arxiv:1']));
    expect(html).toContain('<a href="/papers/arxiv:1">[arxiv:1]</a>');
    expect(html).toContain('[arxiv:2]');
    expect(html).not.toContain('href="/papers/arxiv:2"');
  });

  it('turns blank lines into paragraphs and single breaks into br', () => {
    const html = renderIssueBody('one\ntwo\n\nthree', new Set());
    expect(html).toBe('<p>one<br />two</p><p>three</p>');
  });

  it('renders inline math in the prose', () => {
    const html = renderIssueBody('scaling as $O(n \\log n)$ [arxiv:1]', new Set(['arxiv:1']));
    expect(html).toContain('katex');
    expect(html).toContain('<a href="/papers/arxiv:1">[arxiv:1]</a>');
  });

  it('keeps broken TeX visible as escaped source', () => {
    const html = renderIssueBody('bad $\\frac{$ math', new Set());
    expect(html).not.toContain('<script');
    expect(html).toContain('$');
  });

  it('never lets markup through inside or around citations', () => {
    const html = renderIssueBody('<img src=x onerror=1> [arxiv:1] <svg>', new Set(['arxiv:1']));
    expect(html).not.toContain('<img');
    expect(html).not.toContain('<svg');
    expect(html).toContain('&lt;img src=x onerror=1&gt;');
  });
});
