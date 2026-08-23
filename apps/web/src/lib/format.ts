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
