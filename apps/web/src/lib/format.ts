// Compact numeric rendering for the catalogue. One implementation - the models table and
// the model page used to carry their own copies, drifting only in how they spelled an
// absent value.

/** 1.76e12 parameters reads as "1.76T"; a number that long reads as nothing. */
export function compact(value: number | null, empty = ''): string {
  if (value === null) return empty;
  for (const [size, suffix] of [
    [1e12, 'T'],
    [1e9, 'B'],
    [1e6, 'M'],
    [1e3, 'K'],
  ] as const) {
    if (value >= size) return `${(value / size).toFixed(value / size < 10 ? 1 : 0)}${suffix}`;
  }
  return String(Math.round(value));
}

/** Training compute spans twelve orders of magnitude, so it is shown as one. */
export function exponent(value: number | null): string {
  if (value === null || value <= 0) return '';
  return `1e${Math.round(Math.log10(value))}`;
}

/** Whether the weights are yours to run, in one word. */
export function weightsLabel(open: boolean | null): string {
  if (open === null) return 'unknown';
  return open ? 'open' : 'closed';
}

/** The ISO week number out of a weekly digest slug ("2026-w16" -> 16); null for the
 * daily reports' date slugs, which carry no issue numbering. */
export function issueNumber(slug: string): number | null {
  const match = /-w(\d+)$/.exec(slug);
  return match ? Number(match[1]) : null;
}

/** An ISO date as prose ("2026-09-01" -> "Sep 1, 2026"); the raw string when unparseable. */
export function humanDate(iso: string): string {
  const day = iso.slice(0, 10);
  const parsed = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

const SIGNAL_LABELS: Record<string, string> = {
  citation: 'citations',
  code_stars: 'code stars',
  hf_trending_rank: 'HF trending',
  social_mention: 'social buzz',
  review_score: 'review scores',
  discussion: 'discussion',
};

/** Why a digest item ranked, from its per-signal contributions: the top two positive
 * signals as prose ("Momentum from citations and code stars"); empty when nothing did. */
export function whyLine(why: Record<string, number> | undefined): string {
  const positive = Object.entries(why ?? {})
    .filter(([, value]) => value > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2)
    .map(([key]) => SIGNAL_LABELS[key] ?? key.replaceAll('_', ' '));
  if (positive.length === 0) return '';
  return `Momentum from ${positive.join(' and ')}`;
}

/** The one-line issue summary shared by the archive and the Atom feed; empty at zero. */
export function issueSummary(itemCount: number): string {
  if (itemCount <= 0) return '';
  return `${itemCount} paper${itemCount === 1 ? '' : 's'}`;
}
