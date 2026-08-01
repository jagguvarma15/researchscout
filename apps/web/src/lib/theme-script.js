// The one inline script on the site: it sets the theme before first paint, so there is no
// flash of the wrong one. It lives here as a string rather than in the layout because the
// content policy has to carry its exact hash, and the only way those two cannot drift is if
// the layout and the policy read the same value.
//
// Plain JavaScript, not TypeScript: astro.config.mjs imports it too.

export const THEME_SCRIPT = `
      // Theme before first paint: stored choice wins, otherwise the OS preference.
      const stored = localStorage.getItem('rs-theme');
      document.documentElement.dataset.theme =
        stored === 'dark' || stored === 'light'
          ? stored
          : window.matchMedia('(prefers-color-scheme: dark)').matches
            ? 'dark'
            : 'light';
    `;
