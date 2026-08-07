// Fail the build when a Svelte component's scoped styles are missing from the extracted CSS.
//
// Astro's client build prunes the styles of Svelte components that never render during SSR
// (withastro/astro#9093) - which is how the whole chat surface shipped unstyled while
// `astro build` exited 0. Base.astro carries hidden render anchors as the workaround; this
// check is what turns a regression - an anchor removed, the bug recurring upstream, a new
// island child forgotten - into a red build instead of an unstyled production page.
//
// Sentinels are one distinctive scoped selector per component: present means the component's
// whole style block was extracted somewhere.

import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const dir = fileURLToPath(new URL('../dist/client/_astro', import.meta.url));

const SENTINELS = [
  ['ScoutPanel', '.thread.svelte-'],
  ['ChatMessage', '.msg.svelte-'],
  ['WebHitCard', '.webhit.svelte-'],
  ['SideRail', '.rail.svelte-'],
  ['Omnibox', '.field.svelte-'],
];

let css = '';
let files = 0;
try {
  for (const name of readdirSync(dir)) {
    if (name.endsWith('.css')) {
      css += readFileSync(join(dir, name), 'utf8');
      files += 1;
    }
  }
} catch (error) {
  console.error(`verify-styles: cannot read ${dir} - run astro build first (${error.message})`);
  process.exit(1);
}

const missing = SENTINELS.filter(([, selector]) => !css.includes(selector));
if (missing.length > 0) {
  console.error(
    `verify-styles: ${missing.length} component style block(s) missing from the built CSS ` +
      `(${files} file(s) checked):`,
  );
  for (const [component, selector] of missing) {
    console.error(`  ${component} - no rule matching "${selector}"`);
  }
  console.error('See the CSS-anchor comment in src/layouts/Base.astro.');
  process.exit(1);
}
console.log(`verify-styles: all ${SENTINELS.length} component sentinels present in ${files} CSS file(s).`);
