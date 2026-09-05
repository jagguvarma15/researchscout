// The topics list controls: trend chips narrow and sort pills reorder, both carried in the URL
// so a filtered view is shareable like every other list here. Pure so the filter and sort can be
// tested without rendering the page.

export const TREND_KEYS = ['new', 'rising', 'steady', 'fading'] as const;
export type TrendKey = (typeof TREND_KEYS)[number];
export type TopicSort = 'score' | 'size';

/** An allowed trend key, or '' (every topic) when the param is missing or unknown. */
export function resolveTrend(param: string | null): TrendKey | '' {
  return (TREND_KEYS as readonly string[]).includes(param ?? '') ? (param as TrendKey) : '';
}

/** 'size' only when asked for by name; 'score' (momentum) is the default order. */
export function resolveSort(param: string | null): TopicSort {
  return param === 'size' ? 'size' : 'score';
}

/** The shareable URL for a trend/sort selection; defaults are omitted so the bare path is clean. */
export function controlHref(trend: string, sort: string): string {
  const next = new URLSearchParams();
  if (trend) next.set('trend', trend);
  if (sort !== 'score') next.set('sort', sort);
  const query = next.toString();
  return query ? `/topics?${query}` : '/topics';
}

/** Apply the current view to the topic list: filter by trend, then reorder by size when asked. */
export function applyTopicView<T extends { trend: string | null; size: number }>(
  topics: T[],
  view: { trend: string; sort: string },
): T[] {
  let out = view.trend ? topics.filter((topic) => topic.trend === view.trend) : topics;
  if (view.sort === 'size') out = [...out].sort((a, b) => b.size - a.size);
  return out;
}
