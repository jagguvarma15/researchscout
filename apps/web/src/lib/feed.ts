// The Atom document behind /feeds/digests.xml: pure string building so the shape is
// testable without a server. Everything interpolated is escaped - titles come from an
// LLM-assisted pipeline and the feed must stay well-formed whatever they contain.

import { timingSafeEqual } from 'node:crypto';

export interface FeedIssue {
  slug: string;
  title: string;
  /** ISO timestamp the reader sorts by. */
  updated: string;
  summary?: string;
}

export function escapeXml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;');
}

export function atomFeed(input: {
  siteUrl: string;
  title: string;
  subtitle: string;
  selfPath: string;
  issues: FeedIssue[];
}): string {
  const site = input.siteUrl.replace(/\/+$/, '');
  const updated = input.issues[0]?.updated ?? new Date(0).toISOString();
  const entries = input.issues
    .map((issue) => {
      const url = `${site}/digests/${encodeURIComponent(issue.slug)}`;
      const summary = issue.summary
        ? `\n    <summary>${escapeXml(issue.summary)}</summary>`
        : '';
      return `  <entry>
    <id>${escapeXml(url)}</id>
    <title>${escapeXml(issue.title)}</title>
    <link href="${escapeXml(url)}"/>
    <updated>${escapeXml(issue.updated)}</updated>${summary}
  </entry>`;
    })
    .join('\n');
  return `<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>${escapeXml(`${site}${input.selfPath}`)}</id>
  <title>${escapeXml(input.title)}</title>
  <subtitle>${escapeXml(input.subtitle)}</subtitle>
  <link href="${escapeXml(`${site}${input.selfPath}`)}" rel="self"/>
  <link href="${escapeXml(site)}"/>
  <updated>${escapeXml(updated)}</updated>
${entries}
</feed>
`;
}

/** Constant-time token check, the same recipe as the entry gate's codeMatches. */
export function feedTokenMatches(candidate: string | null, expected: string | undefined): boolean {
  if (!candidate || !expected) return false;
  const a = Buffer.from(candidate, 'utf8');
  const b = Buffer.from(expected, 'utf8');
  // Length is not secret; content is. Equal-length compare only, no early content exit.
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}
