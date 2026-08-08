// Pager arithmetic, out of page frontmatter so it can be tested. The feed and the
// catalogue both paginate; this is the one place that decides which numbers show.

/**
 * The page numbers a pager shows: both endpoints plus a window around the current page,
 * with `null` marking each gap. Endpoints are always kept so the reader can jump to the
 * start or the end from anywhere.
 */
export function pageNumbers(current: number, count: number): (number | null)[] {
  const wanted = new Set([1, count, current - 1, current, current + 1]);
  const pages = [...wanted].filter((p) => p >= 1 && p <= count).sort((a, b) => a - b);
  const out: (number | null)[] = [];
  let prev = 0;
  for (const p of pages) {
    if (p - prev > 1) out.push(null);
    out.push(p);
    prev = p;
  }
  return out;
}

/** The current URL with only the page param changed - every filter survives paging. */
export function pageLink(search: URLSearchParams, target: number, basePath = '/'): string {
  const next = new URLSearchParams(search);
  next.set('page', String(target));
  return `${basePath}?${next}`;
}
