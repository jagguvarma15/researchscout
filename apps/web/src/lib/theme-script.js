// The one inline script on the site: it applies every appearance preference before first
// paint - theme, accent, type size, density, motion - so there is no flash of the wrong
// look, and it keeps two promises across client-router navigations: the preferences survive
// the swap, and a slow navigation shows a progress bar. It lives here as a string rather
// than in the layout because the content policy has to carry its exact hash, and the only
// way those two cannot drift is if the layout and the policy read the same value.
//
// The whitelists are deliberately duplicated from lib/prefs.ts for the same reason: this
// string must stay self-contained so its hash covers everything it does.
//
// Plain JavaScript, not TypeScript: astro.config.mjs imports it too.

export const THEME_SCRIPT = `
      // Stored choices win; an absent or malformed value means the default (and for the
      // theme, the OS preference).
      const readPrefs = () => {
        try {
          const raw = JSON.parse(localStorage.getItem('rs-prefs') || 'null');
          return raw && raw.v === 1 ? raw : {};
        } catch {
          return {};
        }
      };
      const pick = (value, allowed) => (allowed.indexOf(value) === -1 ? null : value);
      const applyPrefs = () => {
        const root = document.documentElement;
        let stored = null;
        try {
          stored = localStorage.getItem('rs-theme');
        } catch {}
        const theme =
          stored === 'dark' || stored === 'light'
            ? stored
            : window.matchMedia('(prefers-color-scheme: dark)').matches
              ? 'dark'
              : 'light';
        root.dataset.theme = theme;
        // The browser-chrome color follows the resolved theme. The static metas key on the
        // OS media query, which an explicit choice diverges from - so both get the resolved
        // value (identical contents make the media split harmless).
        const chrome = theme === 'dark' ? '#191713' : '#faf7f1';
        document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => {
          meta.setAttribute('content', chrome);
        });
        const prefs = readPrefs();
        const set = (name, value) => {
          if (value) root.setAttribute('data-' + name, value);
          else root.removeAttribute('data-' + name);
        };
        set('accent', pick(prefs.accent, ['forest', 'ocean', 'plum']));
        set('fontsize', pick(prefs.fontSize, ['small', 'large']));
        set('density', pick(prefs.density, ['compact']));
        set('motion', pick(prefs.motion, ['reduced']));
      };
      applyPrefs();
      // The client router replaces every attribute on <html> and the head's metas during
      // its swap, and an executed inline script is never re-run - so without this listener
      // the first soft navigation wipes every choice back to the defaults. The listener is
      // registered once and survives every swap.
      document.addEventListener('astro:after-swap', applyPrefs);
      // The navigation progress bar (drawn by CSS on html[data-navigating]). Armed only
      // after a short delay so instant navigations never flash it; the swap itself strips
      // the attribute with the rest, which is exactly when the bar should go.
      let navTimer;
      document.addEventListener('astro:before-preparation', () => {
        clearTimeout(navTimer);
        navTimer = setTimeout(() => {
          document.documentElement.setAttribute('data-navigating', '');
        }, 120);
      });
      document.addEventListener('astro:page-load', () => {
        clearTimeout(navTimer);
        document.documentElement.removeAttribute('data-navigating');
      });
    `;
