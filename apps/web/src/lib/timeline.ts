// Month sub-headers for a dated list: each entry carries a header only when its month differs
// from the previous entry's, so the template groups without comparing dates itself. Pure so the
// grouping is testable without rendering the timeline.

export function monthOf(published: string | null): string {
  if (!published) return 'Undated';
  return new Date(published).toLocaleDateString('en-US', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

export function withMonthHeaders<T extends { published_on: string | null }>(
  items: T[],
): (T & { monthHeader: string | null })[] {
  return items.map((item, index) => ({
    ...item,
    monthHeader:
      index === 0 || monthOf(item.published_on) !== monthOf(items[index - 1].published_on)
        ? monthOf(item.published_on)
        : null,
  }));
}
