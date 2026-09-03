// The Atom feed of digests and daily reports, for feed readers that cannot sign in.
//
// The site is members-only, so the feed carries its own key: a single FEED_TOKEN in the
// environment, compared constant-time, carried as ?token= in the URL a member pastes into
// their reader. No token configured means no feed at all, and a wrong token answers the
// same 404 as a missing page - the URL never advertises that there is something behind it.
// The middleware lets /feeds/digests.xml through to here precisely because this check is
// the gate for that one path.

import type { APIRoute } from 'astro';

import { fetchDigests } from '../../lib/api';
import { atomFeed, feedTokenMatches } from '../../lib/feed';
import { issueSummary } from '../../lib/format';
import { SITE_URL } from '../../lib/site-url.js';

export const GET: APIRoute = async ({ url }) => {
  if (!feedTokenMatches(url.searchParams.get('token'), process.env.FEED_TOKEN)) {
    return new Response('Not found', { status: 404 });
  }
  const result = await fetchDigests();
  if (!result.ok) {
    return new Response('Feed source unavailable', { status: 503 });
  }
  const body = atomFeed({
    siteUrl: SITE_URL,
    title: 'ResearchScout digests',
    subtitle: 'Weekly digests and daily reports from the paper radar.',
    selfPath: '/feeds/digests.xml',
    issues: result.data.items.map((digest) => ({
      slug: digest.slug,
      title: digest.title,
      updated: digest.period_end,
      summary: issueSummary(digest.item_count) || undefined,
    })),
  });
  return new Response(body, {
    headers: {
      'content-type': 'application/atom+xml; charset=utf-8',
      // Readers poll; a short shared cache keeps a fleet of them off the API.
      'cache-control': 'private, max-age=900',
    },
  });
};
