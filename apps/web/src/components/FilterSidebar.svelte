<script lang="ts">
  // The filter sidebar: a left panel over a blurred backdrop, opened by the feed toolbar's
  // [data-open-filters] button. URL-driven by design — Extract serializes the controls into
  // query params and navigates, so results are server-rendered, shareable, and the back
  // button works. Nothing applies live.

  import { navigate } from 'astro:transitions/client';
  import { SlidersHorizontal, X } from 'lucide-svelte';
  import { onDestroy } from 'svelte';

  import { lockBodyScroll, trapFocus } from '../lib/overlay';
  import { SUBJECTS, subjectCategories, TOPICS } from '../lib/taxonomy';

  interface Filters {
    subjects: string[];
    topics: string[];
    categories: string[];
    days: string;
    year: string;
    month: string;
    sort: string;
    minCitations: string;
    author: string;
    venue: string;
  }

  let { initial }: { initial: Filters } = $props();

  let open = $state(false);
  let tab = $state<'subjects' | 'refine' | 'people'>('subjects');
  let panel: HTMLElement | undefined = $state();
  let previousFocus: Element | null = null;
  let unlockScroll: (() => void) | null = null;

  // Applying filters now navigates through the client router, which destroys this island
  // while it is open - the scroll lock must not outlive it on the next page's body.
  onDestroy(() => unlockScroll?.());

  const knownCodes = new Set(
    SUBJECTS.flatMap((subject) => subjectCategories(subject.key).map((cat) => cat.code)),
  );

  let subjects = $state<string[]>([...initial.subjects]);
  let topics = $state<string[]>([...initial.topics]);
  let selectedCats = $state<string[]>(initial.categories.filter((cat) => knownCodes.has(cat)));
  let categoriesText = $state(initial.categories.filter((cat) => !knownCodes.has(cat)).join(', '));
  let dateMode = $state<'days' | 'calendar'>(initial.year ? 'calendar' : 'days');
  let days = $state(initial.days);
  let year = $state(initial.year);
  let month = $state(initial.month);
  let sort = $state(initial.sort || 'newest');
  let minCitations = $state(initial.minCitations);
  let author = $state(initial.author);
  let venue = $state(initial.venue);

  const currentYear = new Date().getFullYear();
  const years = Array.from({ length: currentYear - 2006 }, (_, i) => String(currentYear - i));
  const months = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
  ];

  function toggleSubject(key: string) {
    subjects = subjects.includes(key) ? subjects.filter((s) => s !== key) : [...subjects, key];
  }

  function toggleTopic(key: string) {
    topics = topics.includes(key) ? topics.filter((t) => t !== key) : [...topics, key];
  }

  function toggleCat(code: string) {
    selectedCats = selectedCats.includes(code)
      ? selectedCats.filter((c) => c !== code)
      : [...selectedCats, code];
  }

  // Applied once at render; native <details> toggling takes over from there.
  function initiallyExpanded(subjectKey: string): boolean {
    return subjectCategories(subjectKey).some((cat) => initial.categories.includes(cat.code));
  }

  function show() {
    previousFocus = document.activeElement;
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
    if (open) panel?.querySelector<HTMLElement>('button, input')?.focus();
  });

  function onDocumentClick(event: MouseEvent) {
    if ((event.target as Element).closest('[data-open-filters]')) {
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

  /**
   * Remember the filter state, so the next visit can offer it back.
   *
   * Not awaited and never blocking: the navigation below is already happening, and a cache
   * write must not be in front of it. A signed-out visitor gets a 401 that costs nothing.
   */
  function rememberFilters(query: string) {
    void fetch('/api/me/filters', {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query_string: query }),
      keepalive: true,
    }).catch(() => undefined);
  }

  function extract() {
    const params = new URLSearchParams();
    // The search term rides along. Building the query from nothing dropped it, so opening the
    // filters while searching for something and pressing Extract silently abandoned the search
    // - and the toolbar's technique toggles, which preserve every parameter, disagreed.
    const searching = new URLSearchParams(window.location.search).get('q');
    if (searching) params.set('q', searching);
    for (const key of subjects) params.append('subject', key);
    for (const key of topics) params.append('topic', key);
    const textCodes = categoriesText.split(/[\s,]+/).filter(Boolean);
    for (const cat of [...new Set([...selectedCats, ...textCodes])]) {
      params.append('category', cat);
    }
    if (dateMode === 'days' && days) params.set('days', days);
    if (dateMode === 'calendar' && year) {
      params.set('year', year);
      if (month) params.set('month', month);
    }
    if (sort && sort !== 'newest') params.set('sort', sort);
    if (minCitations) params.set('min_citations', minCitations);
    if (author.trim()) params.set('author', author.trim());
    if (venue.trim()) params.set('venue', venue.trim());
    const query = params.toString();
    rememberFilters(query);
    // Through the client router, so applying filters animates the feed change instead of
    // reloading the document.
    void navigate(query ? `/?${query}` : '/');
  }

  function reset() {
    // Clearing the filters clears what is remembered too, or the next visit would offer back
    // the very thing that was just thrown away.
    rememberFilters('');
    void navigate('/');
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
      class="panel"
      role="dialog"
      aria-modal="true"
      aria-label="Filter papers"
      bind:this={panel}
      onkeydown={onPanelKeydown}
    >
      <header>
        <SlidersHorizontal size={17} aria-hidden="true" />
        <strong>Filter papers</strong>
        <button class="close" onclick={hide} aria-label="Close filters">
          <X size={18} aria-hidden="true" />
        </button>
      </header>

      <div class="tabs" role="tablist" aria-label="Filter groups">
        <button
          role="tab"
          class:active={tab === 'subjects'}
          aria-selected={tab === 'subjects'}
          onclick={() => (tab = 'subjects')}
        >
          Subjects
        </button>
        <button
          role="tab"
          class:active={tab === 'refine'}
          aria-selected={tab === 'refine'}
          onclick={() => (tab = 'refine')}
        >
          Refine
        </button>
        <button
          role="tab"
          class:active={tab === 'people'}
          aria-selected={tab === 'people'}
          onclick={() => (tab = 'people')}
        >
          People and venues
        </button>
      </div>

      <div class="content">
        {#if tab === 'subjects'}
          <fieldset>
            <legend>Technique</legend>
            <p class="hint">A paper can use more than one.</p>
            <div class="segmented" role="group" aria-label="Technique">
              {#each TOPICS as option}
                <button
                  class:on={topics.includes(option.key)}
                  title={option.label}
                  aria-pressed={topics.includes(option.key)}
                  onclick={() => toggleTopic(option.key)}
                >
                  {option.short}
                </button>
              {/each}
            </div>
          </fieldset>
          <fieldset>
            <legend>Field</legend>
            <p class="hint">
              The first four are what this radar is about; the rest are where it meets other
              fields.
            </p>
            {#each SUBJECTS as option, index}
              {#if index === 4}
                <p class="group-break">Intersections</p>
              {/if}
              <label class="check">
                <input
                  type="checkbox"
                  checked={subjects.includes(option.key)}
                  onchange={() => toggleSubject(option.key)}
                />
                {option.label}
              </label>
              {#if subjectCategories(option.key).length > 0}
                <details class="cats" open={initiallyExpanded(option.key)}>
                  <summary>Categories</summary>
                  {#each subjectCategories(option.key) as cat}
                    <label class="check sub">
                      <input
                        type="checkbox"
                        checked={selectedCats.includes(cat.code)}
                        onchange={() => toggleCat(cat.code)}
                      />
                      {cat.code} - {cat.name}
                    </label>
                  {/each}
                </details>
              {/if}
            {/each}
          </fieldset>
          <fieldset>
            <legend>Specific categories</legend>
            <input
              class="input"
              type="text"
              placeholder="math.CO, quant-ph"
              aria-label="Specific categories, comma separated"
              bind:value={categoriesText}
            />
          </fieldset>
        {:else if tab === 'refine'}
          <fieldset>
            <legend>Date</legend>
            <label class="check">
              <input type="radio" name="datemode" value="days" bind:group={dateMode} />
              Last
              <input
                class="input small"
                type="number"
                min="1"
                max="365"
                bind:value={days}
                disabled={dateMode !== 'days'}
                aria-label="Days back"
              />
              days
            </label>
            <label class="check">
              <input type="radio" name="datemode" value="calendar" bind:group={dateMode} />
              Year
              <select
                class="input small"
                bind:value={year}
                disabled={dateMode !== 'calendar'}
                aria-label="Year"
              >
                <option value="">any</option>
                {#each years as y}
                  <option value={y}>{y}</option>
                {/each}
              </select>
              <select
                class="input small"
                bind:value={month}
                disabled={dateMode !== 'calendar' || !year}
                aria-label="Month"
              >
                <option value="">whole year</option>
                {#each months as name, index}
                  <option value={String(index + 1)}>{name}</option>
                {/each}
              </select>
            </label>
          </fieldset>
          <fieldset>
            <legend>Sort</legend>
            <label class="check">
              <input type="radio" name="sort" value="newest" bind:group={sort} /> Newest
            </label>
            <label class="check">
              <input type="radio" name="sort" value="citations" bind:group={sort} /> Most cited
            </label>
            <label class="check">
              <input type="radio" name="sort" value="activity" bind:group={sort} /> Most active
            </label>
          </fieldset>
          <fieldset>
            <legend>Citations</legend>
            <label class="check">
              Min citations
              <input
                class="input small"
                type="number"
                min="0"
                bind:value={minCitations}
                aria-label="Minimum citations"
              />
            </label>
          </fieldset>
        {:else}
          <fieldset>
            <legend>Author contains</legend>
            <input class="input" type="text" placeholder="lovelace" bind:value={author} />
          </fieldset>
          <fieldset>
            <legend>Venue contains</legend>
            <input class="input" type="text" placeholder="NeurIPS" bind:value={venue} />
            <p class="hint">
              Venue comes from journal references and is sparse on arXiv; acceptance notes
              usually live in the comment shown on cards.
            </p>
          </fieldset>
        {/if}
      </div>

      <footer>
        <button class="btn btn-primary" onclick={extract}>Extract</button>
        <button class="btn btn-ghost" onclick={reset}>Reset</button>
      </footer>
    </aside>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 35;
    background: rgb(0 0 0 / 0.32);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    /* The rail slides; this used to pop. Same vocabulary now - fade the veil, slide the
       sheet - and the global motion guard stills both under reduced motion. */
    animation: sidebar-fade var(--dur-fast, 0.15s) var(--ease-out, ease);
  }
  .panel {
    position: absolute;
    top: 0;
    left: 0;
    height: 100dvh;
    width: min(26rem, 100vw);
    display: flex;
    flex-direction: column;
    background: var(--surface, #fff);
    border-right: 1px solid var(--line, #e6e1d5);
    box-shadow: var(--shadow-md, 0 12px 32px rgb(23 25 28 / 0.12));
    animation: sidebar-in var(--dur-slow, 0.25s) var(--ease-out, ease);
  }
  @keyframes sidebar-fade {
    from {
      opacity: 0;
    }
  }
  @keyframes sidebar-in {
    from {
      transform: translateX(-1.5rem);
      opacity: 0;
    }
  }
  header {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 1rem 1.25rem;
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
  .tabs {
    display: flex;
    border-bottom: 1px solid var(--line, #e6e1d5);
    padding: 0 0.75rem;
  }
  .tabs button {
    padding: 0.6rem 0.65rem;
    border: none;
    border-bottom: 2px solid transparent;
    background: none;
    color: var(--muted, #5d6570);
    font: inherit;
    font-size: 0.85rem;
    cursor: pointer;
  }
  .tabs button.active {
    color: var(--ink, #17191c);
    border-bottom-color: var(--accent, #c2410c);
  }
  .content {
    flex: 1;
    overflow-y: auto;
    padding: 1rem 1.25rem;
  }
  fieldset {
    margin: 0 0 1.1rem;
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
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-full, 999px);
    overflow: hidden;
  }
  .segmented button {
    padding: 0.4rem 0.9rem;
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
  }
  .check {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.25rem 0;
    color: var(--ink, #17191c);
    font-size: 0.9rem;
  }
  /* Separates the four core fields from the five where this radar meets other disciplines. */
  .group-break {
    margin: 0.9rem 0 0.2rem;
    color: var(--muted, #5d6570);
    font-size: 0.7rem;
    font-weight: 650;
    letter-spacing: 0.07em;
    text-transform: uppercase;
  }
  .cats {
    margin: 0 0 0.4rem 1.55rem;
  }
  .cats summary {
    color: var(--muted, #5d6570);
    font-size: 0.8rem;
    cursor: pointer;
  }
  .check.sub {
    padding: 0.2rem 0;
    font-size: 0.85rem;
  }
  .check input[type='checkbox'],
  .check input[type='radio'] {
    accent-color: var(--accent, #c2410c);
  }
  .input.small {
    width: 6rem;
    padding: 0.3rem 0.5rem;
    font-size: 0.85rem;
  }
  .hint {
    margin: 0.4rem 0 0.5rem;
    color: var(--muted, #5d6570);
    font-size: 0.8rem;
    line-height: 1.5;
  }
  footer {
    display: flex;
    gap: 0.5rem;
    padding: 1rem 1.25rem calc(1rem + env(safe-area-inset-bottom));
    border-top: 1px solid var(--line, #e6e1d5);
  }
  footer .btn {
    flex: 1;
    justify-content: center;
  }
</style>
