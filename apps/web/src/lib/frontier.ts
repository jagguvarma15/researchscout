// Each topic-history build links into the feed windowed back to that build's day, so "what did
// this topic look like then" is one click. Injecting "now" keeps the day math testable; the
// clamp matches the API's window bounds (1..365 days).

export function windowHref(builtAt: string, now: number = Date.now()): string {
  const age = Math.ceil((now - new Date(builtAt).getTime()) / 86_400_000);
  return `/?days=${Math.min(Math.max(age, 1), 365)}`;
}
