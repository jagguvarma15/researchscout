<script lang="ts">
  // The settings drawer: every site preference in one right-hand sheet, opened from the
  // avatar menu or the rail by anything carrying [data-open-settings]. Appearance choices
  // apply the moment they are clicked - the page is the preview - and persist per device
  // through lib/prefs. The feed defaults group writes a cookie instead, because the server
  // is who applies those, on the next feed load.
  //
  // Mechanics are FilterSidebar's: document-level trigger delegate, focus trap, counted
  // scroll lock released on destroy, Escape to close.

  import { Keyboard, Settings2, X } from 'lucide-svelte';
  import { onDestroy } from 'svelte';

  import { lockBodyScroll, trapFocus } from '../lib/overlay';
  import {
    applyTheme,
    clearFeedDefaultsCookie,
    readFeedDefaultsCookie,
    readPrefs,
    themeChoice,
    updatePrefs,
    withViewTransition,
    writeFeedDefaultsCookie,
    type Accent,
    type Density,
    type FeedDays,
    type FeedDefaults,
    type FeedSort,
    type FeedTopic,
    type FontSize,
    type Motion,
    type Prefs,
    type ThemeChoice,
  } from '../lib/prefs';
  import { TOPICS } from '../lib/taxonomy';

  let open = $state(false);
  let panel: HTMLElement | undefined = $state();
  let previousFocus: Element | null = null;
  let unlockScroll: (() => void) | null = null;

  onDestroy(() => unlockScroll?.());

  let theme = $state<ThemeChoice>('system');
  let prefs = $state<Prefs>({});
  let feedSort = $state<FeedSort>('newest');
  let feedDays = $state<FeedDays>('7');
  let feedTopic = $state<FeedTopic | ''>('');

  const ACCENT_CHOICES: { value: Accent | null; label: string; dot: string }[] = [
    { value: null, label: 'Amber', dot: 'amber' },
    { value: 'forest', label: 'Forest', dot: 'forest' },
    { value: 'ocean', label: 'Ocean', dot: 'ocean' },
    { value: 'plum', label: 'Plum', dot: 'plum' },
  ];

  const hasFeedDefaults = $derived(feedSort !== 'newest' || feedDays !== '7' || feedTopic !== '');

  function show() {
    previousFocus = document.activeElement;
    // Read everything fresh on open: another tab, the header toggle, or the pre-paint
    // script may have moved things since this island mounted.
    theme = themeChoice();
    prefs = readPrefs();
    const stored = readFeedDefaultsCookie();
    feedSort = stored?.sort ?? 'newest';
    feedDays = stored?.days ?? '7';
    feedTopic = stored?.topic ?? '';
    open = true;
    unlockScroll = lockBodyScroll();
  }

  function hide() {
    open = false;
    unlockScroll?.();
    unlockScroll = null;
    if (previousFocus instanceof HTMLElement) previousFocus.focus();
  }

  $effect(() => {
    if (open) panel?.querySelector<HTMLElement>('button')?.focus();
  });

  function onDocumentClick(event: MouseEvent) {
    if ((event.target as Element).closest('[data-open-settings]')) {
      event.preventDefault();
      show();
    }
  }

  function onWindowKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape' && open) hide();
  }

  function onPanelKeydown(event: KeyboardEvent) {
    if (panel) trapFocus(panel, event);
  }

  // Every setter puts ALL of its mutations inside the view-transition callback - Svelte
  // flushes in a microtask, which the transition waits out before capturing the old
  // state, so a mutation outside the wrap would land in the "old" snapshot and the pill
  // morph would silently no-op. The page cross-fade and the pill glide are one snapshot.
  function setTheme(choice: ThemeChoice) {
    withViewTransition(() => {
      theme = choice;
      applyTheme(choice);
    });
  }

  function setAccent(value: Accent | null) {
    withViewTransition(() => {
      prefs = updatePrefs({ accent: value });
    });
  }

  function setFontSize(value: FontSize | null) {
    withViewTransition(() => {
      prefs = updatePrefs({ fontSize: value });
    });
  }

  function setDensity(value: Density | null) {
    withViewTransition(() => {
      prefs = updatePrefs({ density: value });
    });
  }

  function setMotion(value: Motion | null) {
    // Never animated: engaging stillness must not itself animate, and disengaging runs
    // while the attribute still says reduced anyway.
    prefs = updatePrefs({ motion: value });
  }

  // One name per group, carried by whichever button is on - that is what the browser
  // morphs between the old and new snapshots. At most one .on per group by construction.
  const seg = (on: boolean, name: string) => (on ? `view-transition-name: seg-${name}` : undefined);

  // Only departures from the stock radar are worth storing; an all-stock choice clears the
  // cookie, so the feed never claims defaults that change nothing.
  function syncFeedCookie() {
    const defaults: FeedDefaults = {};
    if (feedSort !== 'newest') defaults.sort = feedSort;
    if (feedDays !== '7') defaults.days = feedDays;
    if (feedTopic !== '') defaults.topic = feedTopic;
    writeFeedDefaultsCookie(defaults);
  }

  function setFeedSort(value: FeedSort) {
    withViewTransition(() => {
      feedSort = value;
      syncFeedCookie();
    });
  }

  function setFeedDays(value: FeedDays) {
    withViewTransition(() => {
      feedDays = value;
      syncFeedCookie();
    });
  }

  function setFeedTopic(value: FeedTopic | '') {
    withViewTransition(() => {
      feedTopic = value;
      syncFeedCookie();
    });
  }

  function clearFeedDefaults() {
    withViewTransition(() => {
      feedSort = 'newest';
      feedDays = '7';
      feedTopic = '';
      clearFeedDefaultsCookie();
    });
  }
</script>

<svelte:document onclick={onDocumentClick} />
<svelte:window onkeydown={onWindowKeydown} />

{#if open}
  <div
    class="backdrop"
    role="presentation"
    onclick={(event) => {
      if (event.target === event.currentTarget) hide();
    }}
  >
    <aside
      class="prefs-panel"
      role="dialog"
      aria-modal="true"
      aria-label="Site settings"
      bind:this={panel}
      onkeydown={onPanelKeydown}
    >
      <header>
        <Settings2 size={17} aria-hidden="true" />
        <strong>Settings</strong>
        <button class="close" onclick={hide} aria-label="Close settings">
          <X size={18} aria-hidden="true" />
        </button>
      </header>

      <div class="content">
        <fieldset>
          <legend>Theme</legend>
          <div class="segmented" role="group" aria-label="Theme">
            <button class:on={theme === 'light'} aria-pressed={theme === 'light'} style={seg(theme === 'light', 'theme')} onclick={() => setTheme('light')}>Light</button>
            <button class:on={theme === 'dark'} aria-pressed={theme === 'dark'} style={seg(theme === 'dark', 'theme')} onclick={() => setTheme('dark')}>Dark</button>
            <button class:on={theme === 'system'} aria-pressed={theme === 'system'} style={seg(theme === 'system', 'theme')} onclick={() => setTheme('system')}>System</button>
          </div>
        </fieldset>

        <fieldset>
          <legend>Accent</legend>
          <div class="swatches" role="group" aria-label="Accent color">
            {#each ACCENT_CHOICES as choice}
              <button
                class="swatch"
                class:on={(prefs.accent ?? null) === choice.value}
                aria-pressed={(prefs.accent ?? null) === choice.value}
                style={seg((prefs.accent ?? null) === choice.value, 'accent')}
                onclick={() => setAccent(choice.value)}
              >
                <span class="dot {choice.dot}" aria-hidden="true"></span>
                {choice.label}
              </button>
            {/each}
          </div>
        </fieldset>

        <fieldset>
          <legend>Text size</legend>
          <div class="segmented" role="group" aria-label="Text size">
            <button class:on={prefs.fontSize === 'small'} aria-pressed={prefs.fontSize === 'small'} style={seg(prefs.fontSize === 'small', 'fontsize')} onclick={() => setFontSize('small')}>Small</button>
            <button class:on={!prefs.fontSize} aria-pressed={!prefs.fontSize} style={seg(!prefs.fontSize, 'fontsize')} onclick={() => setFontSize(null)}>Default</button>
            <button class:on={prefs.fontSize === 'large'} aria-pressed={prefs.fontSize === 'large'} style={seg(prefs.fontSize === 'large', 'fontsize')} onclick={() => setFontSize('large')}>Large</button>
          </div>
        </fieldset>

        <fieldset>
          <legend>Feed density</legend>
          <div class="segmented" role="group" aria-label="Feed density">
            <button class:on={!prefs.density} aria-pressed={!prefs.density} style={seg(!prefs.density, 'density')} onclick={() => setDensity(null)}>Comfortable</button>
            <button class:on={prefs.density === 'compact'} aria-pressed={prefs.density === 'compact'} style={seg(prefs.density === 'compact', 'density')} onclick={() => setDensity('compact')}>Compact</button>
          </div>
          <p class="hint">Compact tightens the paper lists to fit more on screen.</p>
        </fieldset>

        <fieldset>
          <legend>Motion</legend>
          <div class="segmented" role="group" aria-label="Motion">
            <button class:on={!prefs.motion} aria-pressed={!prefs.motion} style={seg(!prefs.motion, 'motion')} onclick={() => setMotion(null)}>Follow system</button>
            <button class:on={prefs.motion === 'reduced'} aria-pressed={prefs.motion === 'reduced'} style={seg(prefs.motion === 'reduced', 'motion')} onclick={() => setMotion('reduced')}>Reduced</button>
          </div>
          <p class="hint">Reduced stills the site's animations without touching your OS setting.</p>
        </fieldset>

        <fieldset>
          <legend>Feed defaults</legend>
          <div class="sub">
            <span class="sublabel">Sort</span>
            <div class="segmented" role="group" aria-label="Default sort">
              <button class:on={feedSort === 'newest'} aria-pressed={feedSort === 'newest'} style={seg(feedSort === 'newest', 'sort')} onclick={() => setFeedSort('newest')}>Newest</button>
              <button class:on={feedSort === 'citations'} aria-pressed={feedSort === 'citations'} style={seg(feedSort === 'citations', 'sort')} onclick={() => setFeedSort('citations')}>Most cited</button>
              <button class:on={feedSort === 'activity'} aria-pressed={feedSort === 'activity'} style={seg(feedSort === 'activity', 'sort')} onclick={() => setFeedSort('activity')}>Most active</button>
            </div>
          </div>
          <div class="sub">
            <span class="sublabel">Window</span>
            <div class="segmented" role="group" aria-label="Default time window">
              <button class:on={feedDays === '7'} aria-pressed={feedDays === '7'} style={seg(feedDays === '7', 'days')} onclick={() => setFeedDays('7')}>7 days</button>
              <button class:on={feedDays === '14'} aria-pressed={feedDays === '14'} style={seg(feedDays === '14', 'days')} onclick={() => setFeedDays('14')}>14</button>
              <button class:on={feedDays === '30'} aria-pressed={feedDays === '30'} style={seg(feedDays === '30', 'days')} onclick={() => setFeedDays('30')}>30</button>
              <button class:on={feedDays === 'all'} aria-pressed={feedDays === 'all'} style={seg(feedDays === 'all', 'days')} onclick={() => setFeedDays('all')}>All time</button>
            </div>
          </div>
          <div class="sub">
            <span class="sublabel">Technique</span>
            <div class="segmented" role="group" aria-label="Default technique">
              <button class:on={feedTopic === ''} aria-pressed={feedTopic === ''} style={seg(feedTopic === '', 'topic')} onclick={() => setFeedTopic('')}>Any</button>
              {#each TOPICS as option}
                <button
                  class:on={feedTopic === option.key}
                  aria-pressed={feedTopic === option.key}
                  title={option.label}
                  style={seg(feedTopic === option.key, 'topic')}
                  onclick={() => setFeedTopic(option.key as 'nlp' | 'cv' | 'rl')}
                >
                  {option.short}
                </button>
              {/each}
            </div>
          </div>
          <p class="hint">
            How the front page opens for you, applied on its next load. A chip there shows
            when these are active and clears back to the stock radar.
          </p>
          {#if hasFeedDefaults}
            <button class="btn btn-ghost clear" onclick={clearFeedDefaults}>Clear feed defaults</button>
          {/if}
        </fieldset>

        <fieldset>
          <legend>Keyboard</legend>
          <button class="btn btn-ghost keys" data-open-shortcuts onclick={hide}>
            <Keyboard size={16} aria-hidden="true" />
            Keyboard shortcuts
          </button>
        </fieldset>
      </div>

      <footer>
        <button class="btn btn-primary" onclick={hide}>Done</button>
      </footer>
    </aside>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    /* Above the rail drawer (38): settings can be reached from inside it, and the last
       surface opened sits on top. The notices at 60 still outrank everything. */
    z-index: 39;
    background: color-mix(in srgb, var(--bg, #faf7f1) 72%, transparent);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    animation: prefs-fade var(--dur-fast, 0.15s) var(--ease-out, ease);
  }
  .prefs-panel {
    position: absolute;
    top: 0;
    right: 0;
    height: 100dvh;
    width: min(24rem, 100vw);
    display: flex;
    flex-direction: column;
    background: var(--surface, #fff);
    border-left: 1px solid var(--line, #e6e1d5);
    box-shadow: var(--shadow-md, 0 12px 32px rgb(23 25 28 / 0.12));
    animation: prefs-in var(--dur-slow, 0.25s) var(--ease-out, ease);
  }
  @keyframes prefs-fade {
    from {
      opacity: 0;
    }
  }
  @keyframes prefs-in {
    from {
      transform: translateX(1.5rem);
      opacity: 0;
    }
  }
  header {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    /* The top inset is live in installed mode, where top: 0 is under the status bar. */
    padding: calc(1rem + env(safe-area-inset-top)) 1.25rem 1rem;
    border-bottom: 1px solid var(--line, #e6e1d5);
    color: var(--ink, #17191c);
  }
  .close {
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 2rem;
    border: none;
    border-radius: 999px;
    background: none;
    cursor: pointer;
    color: var(--muted, #5d6570);
  }
  .close:hover {
    background: var(--surface-2, #f4f0e8);
    color: var(--ink, #17191c);
  }
  .content {
    flex: 1;
    overflow-y: auto;
    padding: 1rem 1.25rem;
  }
  fieldset {
    margin: 0 0 1.15rem;
    padding: 0;
    border: none;
  }
  legend {
    padding: 0;
    margin-bottom: 0.45rem;
    color: var(--muted, #5d6570);
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .segmented {
    display: inline-flex;
    flex-wrap: wrap;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-full, 999px);
    overflow: hidden;
  }
  .segmented button {
    padding: 0.4rem 0.8rem;
    border: none;
    background: none;
    color: var(--muted, #5d6570);
    font: inherit;
    font-size: 0.85rem;
    cursor: pointer;
  }
  .segmented button.on {
    background: var(--accent-soft, #fef3c7);
    color: var(--accent-ink, #78350f);
    font-weight: 600;
    /* Its own radius, not just the group's clipping: the morph snapshots ignore ancestor
       clipping, so without this the pill shows squared corners mid-flight at the group's
       ends. */
    border-radius: var(--radius-full, 999px);
  }
  .swatches {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }
  .swatch {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.4rem 0.8rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-full, 999px);
    background: var(--surface, #fff);
    color: var(--muted, #5d6570);
    font: inherit;
    font-size: 0.85rem;
    cursor: pointer;
  }
  .swatch.on {
    border-color: var(--accent, #c2410c);
    background: var(--accent-soft, #fef3c7);
    color: var(--accent-ink, #78350f);
    font-weight: 600;
  }
  /* The dots stay the light-theme accents in both themes: they are labels for a choice,
     not samples of the current rendering. */
  .dot {
    width: 0.85rem;
    height: 0.85rem;
    border-radius: 999px;
  }
  .dot.amber {
    background: #c2410c;
  }
  .dot.forest {
    background: #15753a;
  }
  .dot.ocean {
    background: #1d4ed8;
  }
  .dot.plum {
    background: #7e22ce;
  }
  .sub {
    margin-bottom: 0.6rem;
  }
  .sublabel {
    display: block;
    margin-bottom: 0.3rem;
    color: var(--ink, #17191c);
    font-size: 0.85rem;
  }
  .hint {
    margin: 0.45rem 0 0;
    color: var(--muted, #5d6570);
    font-size: 0.8rem;
    line-height: 1.5;
  }
  .clear {
    margin-top: 0.6rem;
  }
  .keys {
    gap: 0.45rem;
  }
  footer {
    display: flex;
    padding: 1rem 1.25rem calc(1rem + env(safe-area-inset-bottom));
    border-top: 1px solid var(--line, #e6e1d5);
  }
  footer .btn {
    flex: 1;
    justify-content: center;
  }
  /* Finger sizing where the panel is the whole screen. */
  @media (max-width: 40rem) {
    .segmented button,
    .swatch {
      padding: 0.55rem 0.9rem;
    }
  }
</style>
