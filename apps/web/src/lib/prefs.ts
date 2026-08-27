// The typed side of the preference machinery. The inline pre-paint script in
// theme-script.js owns the first application (and deliberately duplicates the whitelists so
// its CSP hash covers everything it does); this module is what the settings UI calls to
// change things afterwards, and what the feed's server render uses to read its defaults
// cookie. Appearance lives in localStorage because only the browser needs it; the feed
// defaults live in a cookie because the server applies them before the page exists.

import { prefersReducedMotion } from './motion';

export type Accent = 'forest' | 'ocean' | 'plum';
export type FontSize = 'small' | 'large';
export type Density = 'compact';
export type Motion = 'reduced';
export type ThemeChoice = 'light' | 'dark' | 'system';

export interface Prefs {
  accent?: Accent;
  fontSize?: FontSize;
  density?: Density;
  motion?: Motion;
}

export const ACCENTS: readonly Accent[] = ['forest', 'ocean', 'plum'];
export const FONT_SIZES: readonly FontSize[] = ['small', 'large'];
export const DENSITIES: readonly Density[] = ['compact'];
export const MOTIONS: readonly Motion[] = ['reduced'];

const PREFS_KEY = 'rs-prefs';
const THEME_KEY = 'rs-theme';
const VERSION = 1;

function pick<T extends string>(value: unknown, allowed: readonly T[]): T | undefined {
  return allowed.includes(value as T) ? (value as T) : undefined;
}

/** The stored preferences, with anything unrecognized dropped rather than trusted. */
export function readPrefs(): Prefs {
  if (typeof localStorage === 'undefined') return {};
  let raw: unknown;
  try {
    raw = JSON.parse(localStorage.getItem(PREFS_KEY) ?? 'null');
  } catch {
    return {};
  }
  if (typeof raw !== 'object' || raw === null) return {};
  const env = raw as Record<string, unknown>;
  if (env.v !== VERSION) return {};
  const prefs: Prefs = {};
  const accent = pick(env.accent, ACCENTS);
  const fontSize = pick(env.fontSize, FONT_SIZES);
  const density = pick(env.density, DENSITIES);
  const motion = pick(env.motion, MOTIONS);
  if (accent) prefs.accent = accent;
  if (fontSize) prefs.fontSize = fontSize;
  if (density) prefs.density = density;
  if (motion) prefs.motion = motion;
  return prefs;
}

/** Set the html data attributes the stylesheets key on; absent value = attribute removed. */
export function applyPrefAttributes(prefs: Prefs): void {
  const root = document.documentElement;
  const set = (name: string, value: string | undefined) => {
    if (value) root.setAttribute(`data-${name}`, value);
    else root.removeAttribute(`data-${name}`);
  };
  set('accent', prefs.accent);
  set('fontsize', prefs.fontSize);
  set('density', prefs.density);
  set('motion', prefs.motion);
}

/**
 * Change preferences and make them visible at once: null clears a key back to its default,
 * an absent key stays as it is. Returns the resulting preferences for the caller's state.
 */
export function updatePrefs(changes: { [K in keyof Prefs]?: Prefs[K] | null }): Prefs {
  const prefs = readPrefs();
  for (const key of ['accent', 'fontSize', 'density', 'motion'] as const) {
    if (!(key in changes)) continue;
    const value = changes[key];
    if (value === null) delete prefs[key];
    else if (value !== undefined) (prefs as Record<string, string>)[key] = value;
  }
  try {
    if (Object.keys(prefs).length === 0) localStorage.removeItem(PREFS_KEY);
    else localStorage.setItem(PREFS_KEY, JSON.stringify({ v: VERSION, ...prefs }));
  } catch {
    // Quota or private mode: the choice still applies to this page, it just will not
    // survive a reload.
  }
  applyPrefAttributes(prefs);
  return prefs;
}

/**
 * Run a DOM-mutating change inside a view transition, so the page cross-fades to its new
 * appearance instead of cutting. Falls back to a plain call when the API is missing or
 * when either motion switch asks for stillness.
 *
 * Callers wrap their outermost event handler exactly once: every state change that should
 * land in the "new" snapshot belongs inside `mutate` (Svelte flushes in a microtask,
 * which the transition waits out before capturing), and nothing inside may call this
 * again - a nested startViewTransition skips the one in flight.
 */
export function withViewTransition(mutate: () => void): void {
  if (typeof document.startViewTransition !== 'function' || prefersReducedMotion()) {
    mutate();
    return;
  }
  document.startViewTransition(mutate);
}

/** The stored theme choice; absence has always meant "follow the OS". */
export function themeChoice(): ThemeChoice {
  if (typeof localStorage === 'undefined') return 'system';
  try {
    const stored = localStorage.getItem(THEME_KEY);
    return stored === 'light' || stored === 'dark' ? stored : 'system';
  } catch {
    return 'system';
  }
}

/**
 * Apply a theme choice now: persist it (removing the key for "system"), resolve it to a
 * concrete theme, restyle the page and the browser chrome, and tell other islands. The
 * event is what keeps the header toggle honest when the settings drawer changes the theme.
 */
export function applyTheme(choice: ThemeChoice): 'light' | 'dark' {
  try {
    if (choice === 'system') localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, choice);
  } catch {
    // Same stance as above: apply without surviving.
  }
  const resolved =
    choice === 'system'
      ? window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light'
      : choice;
  document.documentElement.dataset.theme = resolved;
  const chrome = resolved === 'dark' ? '#191713' : '#faf7f1';
  for (const meta of document.querySelectorAll('meta[name="theme-color"]')) {
    meta.setAttribute('content', chrome);
  }
  document.dispatchEvent(new CustomEvent('rs:themechange', { detail: { choice, resolved } }));
  return resolved;
}

// --- Feed defaults (a cookie, because the server applies them) ---

export type FeedSort = 'newest' | 'citations' | 'activity';
export type FeedDays = '7' | '14' | '30' | 'all';
export type FeedTopic = 'nlp' | 'cv' | 'rl';

export interface FeedDefaults {
  sort?: FeedSort;
  days?: FeedDays;
  topic?: FeedTopic;
}

export const FEED_SORTS: readonly FeedSort[] = ['newest', 'citations', 'activity'];
export const FEED_DAYS: readonly FeedDays[] = ['7', '14', '30', 'all'];
export const FEED_TOPICS: readonly FeedTopic[] = ['nlp', 'cv', 'rl'];

// Deliberately device-level, not scoped to the signed-in account (unlike the Scout
// transcript, which carries an owner tag): the cookie holds three enum preferences with no
// personal data and nothing another account could learn from, the same class as rs-theme.
export const FEED_DEFAULTS_COOKIE = 'rs-feed-defaults';

/**
 * Parse a cookie value into feed defaults. Pure and forgiving: the value may arrive raw or
 * URI-encoded, anything malformed or unrecognized is dropped, and a result with nothing
 * left in it is null so callers can treat "no defaults" as one case. Never throws - a bad
 * cookie must not take down the server render that reads it.
 */
export function parseFeedDefaults(raw: string | null | undefined): FeedDefaults | null {
  if (!raw) return null;
  let parsed: unknown = null;
  for (const candidate of [raw, safeDecode(raw)]) {
    if (candidate === null) continue;
    try {
      parsed = JSON.parse(candidate);
      break;
    } catch {
      continue;
    }
  }
  if (typeof parsed !== 'object' || parsed === null) return null;
  const env = parsed as Record<string, unknown>;
  const defaults: FeedDefaults = {};
  const sort = pick(env.sort, FEED_SORTS);
  const days = pick(env.days, FEED_DAYS);
  const topic = pick(env.topic, FEED_TOPICS);
  if (sort) defaults.sort = sort;
  if (days) defaults.days = days;
  if (topic) defaults.topic = topic;
  return Object.keys(defaults).length > 0 ? defaults : null;
}

function safeDecode(raw: string): string | null {
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}

export function serializeFeedDefaults(defaults: FeedDefaults): string {
  return encodeURIComponent(JSON.stringify(defaults));
}

/** Persist feed defaults for a year; an empty object clears the cookie instead. */
export function writeFeedDefaultsCookie(defaults: FeedDefaults): void {
  if (Object.keys(defaults).length === 0) {
    clearFeedDefaultsCookie();
    return;
  }
  document.cookie = `${FEED_DEFAULTS_COOKIE}=${serializeFeedDefaults(defaults)}; Path=/; Max-Age=31536000; SameSite=Lax`;
}

export function clearFeedDefaultsCookie(): void {
  document.cookie = `${FEED_DEFAULTS_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
}

/** The current feed defaults as the browser sees them, for the settings UI's initial state. */
export function readFeedDefaultsCookie(): FeedDefaults | null {
  if (typeof document === 'undefined') return null;
  const entry = document.cookie
    .split('; ')
    .find((part) => part.startsWith(`${FEED_DEFAULTS_COOKIE}=`));
  return parseFeedDefaults(entry ? entry.slice(FEED_DEFAULTS_COOKIE.length + 1) : null);
}
