// The relative bar shown on a sorted numeric column: a value's share of the column maximum,
// linear by default and log-scaled for compute (which spans twelve orders of magnitude and
// reads on a log bar, matching its exponent rendering).

export function barWidth(
  value: number | null,
  max: number,
  options: { log?: boolean } = {},
): number | null {
  if (value === null || value <= 0 || max <= 0) return null;
  if (options.log) {
    const top = Math.log10(max);
    return top > 0 ? Math.round((Math.log10(value) / top) * 100) : null;
  }
  return Math.round((value / max) * 100);
}
