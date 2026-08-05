// The one inline script on the site: it sets the theme before first paint, so there is no
// flash of the wrong one, and it keeps two promises across client-router navigations - the
// theme survives the swap, and a slow navigation shows a progress bar. It lives here as a
// string rather than in the layout because the content policy has to carry its exact hash,
// and the only way those two cannot drift is if the layout and the policy read the same
// value.
//
// Plain JavaScript, not TypeScript: astro.config.mjs imports it too.

export const THEME_SCRIPT = `
      // Theme before first paint: stored choice wins, otherwise the OS preference.
      const applyTheme = () => {
        const stored = localStorage.getItem('rs-theme');
        document.documentElement.dataset.theme =
          stored === 'dark' || stored === 'light'
            ? stored
            : window.matchMedia('(prefers-color-scheme: dark)').matches
              ? 'dark'
              : 'light';
      };
      applyTheme();
      // The client router replaces every attribute on <html> during its swap, including
      // data-theme, and an executed inline script is never re-run - so without this
      // listener the first soft navigation flips a dark page to light. The listener is
      // registered once and survives every swap.
      document.addEventListener('astro:after-swap', applyTheme);
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
