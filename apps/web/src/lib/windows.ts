/* Which look-back windows the For You page offers. With the corpus capped to a rolling
   retention window, a 30- or 90-day pill would show exactly what the window shows, so the
   choices shrink to fit inside it and the window itself becomes the default. */

const STOCK_WINDOWS = [7, 30, 90] as const;
const STOCK_DEFAULT = 30;

export function lookbackWindows(retentionDays: number | null | undefined): number[] {
  if (!retentionDays || retentionDays <= 0) return [...STOCK_WINDOWS];
  const inside = [3, 7].filter((days) => days < retentionDays);
  return [...inside, retentionDays];
}

export function defaultWindow(retentionDays: number | null | undefined): number {
  return retentionDays && retentionDays > 0 ? retentionDays : STOCK_DEFAULT;
}

/* The ?days= value a reader asked for, or null for the default. Any whole number of days the
   API accepts is honored; the API clamps it to the retention window and reports what it used. */
export function parseWindow(raw: string | null): number | null {
  if (raw === null || !/^\d{1,3}$/.test(raw)) return null;
  const days = Number(raw);
  return days >= 1 && days <= 365 ? days : null;
}
