// The one question every animation site asks, with two possible yeses: the OS switch, or
// the site's own setting (html[data-motion], set by the settings drawer and the pre-paint
// script). CSS covers both through the global guard in global.css; this is the same check
// for code that branches before animating. matchMedia is feature-guarded for test DOMs.

export function prefersReducedMotion(): boolean {
  return (
    document.documentElement.dataset.motion === 'reduced' ||
    (typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  );
}
