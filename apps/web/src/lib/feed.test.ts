// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { atomFeed, escapeXml, feedTokenMatches } from './feed';

describe('escapeXml', () => {
  it('escapes the five reserved characters', () => {
    expect(escapeXml(`<a href="x">&'`)).toBe('&lt;a href=&quot;x&quot;&gt;&amp;&apos;');
  });
});

describe('atomFeed', () => {
  const issues = [
    { slug: '2026-w35', title: 'Week 35 <digest> & more', updated: '2026-08-28T12:00:00Z' },
    { slug: '2026-08-27', title: 'Daily report', updated: '2026-08-27T11:00:00Z' },
  ];

  it('builds a well-formed feed with escaped entries and absolute links', () => {
    const xml = atomFeed({
      siteUrl: 'https://scout.example/',
      title: 'ResearchScout digests',
      subtitle: 'The radar, delivered.',
      selfPath: '/feeds/digests.xml',
      issues,
    });
    expect(xml.startsWith('<?xml version="1.0" encoding="utf-8"?>')).toBe(true);
    expect(xml).toContain('<title>Week 35 &lt;digest&gt; &amp; more</title>');
    expect(xml).toContain('<link href="https://scout.example/digests/2026-w35"/>');
    expect(xml).toContain('<link href="https://scout.example/feeds/digests.xml" rel="self"/>');
    // The feed's own updated stamp is the newest issue's.
    expect(xml).toContain('<updated>2026-08-28T12:00:00Z</updated>');
    expect(xml.match(/<entry>/g)).toHaveLength(2);
  });

  it('survives an empty archive', () => {
    const xml = atomFeed({
      siteUrl: 'https://scout.example',
      title: 't',
      subtitle: 's',
      selfPath: '/feeds/digests.xml',
      issues: [],
    });
    expect(xml).toContain('<feed xmlns="http://www.w3.org/2005/Atom">');
    expect(xml).not.toContain('<entry>');
  });
});

describe('feedTokenMatches', () => {
  it('matches only the exact configured token', () => {
    expect(feedTokenMatches('secret-token', 'secret-token')).toBe(true);
    expect(feedTokenMatches('secret-tokeN', 'secret-token')).toBe(false);
    expect(feedTokenMatches('short', 'secret-token')).toBe(false);
  });

  it('never matches when the feature is unconfigured', () => {
    expect(feedTokenMatches('anything', undefined)).toBe(false);
    expect(feedTokenMatches('anything', '')).toBe(false);
    expect(feedTokenMatches(null, 'secret-token')).toBe(false);
    // An empty candidate against an empty expectation is still off, not a match.
    expect(feedTokenMatches('', '')).toBe(false);
  });
});
