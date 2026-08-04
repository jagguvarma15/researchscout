<script lang="ts">
  // One field for finding papers and for asking Scout about them.
  //
  // This replaces two surfaces that used to be learned separately - a Cmd+K command palette
  // and a right-hand chat drawer - even though both began with typing a phrase. The field
  // lives in the header; everything it can do appears in one floating panel beneath it.
  //
  // The panel never forces a choice. Papers matching what you typed are listed as you type,
  // and an ask row is always offered; lib/omnibox.ts only decides which of the two is
  // highlighted first, so guessing wrong costs one arrow key. The conversation itself, and
  // the transport behind it, belong to ScoutPanel.

  import { CornerDownLeft, FileText, Globe, Search, Sparkles, Square } from 'lucide-svelte';

  import type { KeywordCount } from '../lib/chat-types';
  import { commandHint, parseInput } from '../lib/commands';
  import { matchKeywords } from '../lib/keyword-match';
  import { stripMath } from '../lib/math-text';
  import { classify, createSequencer, debounce, searchUrl } from '../lib/omnibox';
  import ScoutMascot from './ScoutMascot.svelte';
  import ScoutPanel from './ScoutPanel.svelte';

  interface PaperHit {
    id: string;
    title: string;
    score: number | null;
  }

  type Entry =
    | { kind: 'ask'; label: string; question: string }
    | { kind: 'web'; label: string; query: string }
    | { kind: 'paper'; label: string; href: string; score: number | null }
    | { kind: 'search'; label: string; href: string }
    | { kind: 'nav'; label: string; href: string };

  // Home only. Every other destination is one glance away in the navigation rail, and a
  // panel that repeats them buries the papers it exists to show.
  const NAV: { href: string; label: string }[] = [{ href: '/', label: 'Home' }];

  const GROUP_LABEL: Record<Entry['kind'], string> = {
    ask: 'Scout',
    web: 'Scout',
    paper: 'Papers',
    search: 'Papers',
    nav: 'Jump to',
  };

  let open = $state(false);
  let query = $state('');
  let papers = $state<PaperHit[]>([]);
  let selected = $state(0);
  let busy = $state(false);
  let asked = $state(false);
  let searching = $state(false);
  let dictionary = $state<KeywordCount[] | null>(null);

  let root: HTMLElement | undefined = $state();
  let inputEl: HTMLInputElement | undefined = $state();
  let body: HTMLElement | undefined = $state();
  let panel: ReturnType<typeof ScoutPanel> | undefined = $state();

  const sequencer = createSequencer();

  const trimmed = $derived(query.trim());
  const intent = $derived(classify(query));
  const hint = $derived(commandHint(query));
  const suggestions = $derived(
    !busy && dictionary && intent !== 'command' && trimmed.length >= 2
      ? matchKeywords(query, dictionary)
      : [],
  );

  const askEntry = $derived<Entry | null>(
    intent === 'command'
      ? commandEntry()
      : trimmed
        ? { kind: 'ask', label: `Ask Scout: "${trimmed}"`, question: trimmed }
        : null,
  );

  function commandEntry(): Entry | null {
    const parsed = parseInput(query);
    if (parsed.kind === 'web' && parsed.query) {
      return { kind: 'web', label: `Search the web for "${parsed.query}"`, query: parsed.query };
    }
    if (parsed.kind === 'ai' && parsed.question) {
      return { kind: 'ask', label: `Ask the AI: "${parsed.question}"`, question: parsed.question };
    }
    return null;
  }

  const paperEntries = $derived<Entry[]>(
    papers.map((hit) => ({
      kind: 'paper' as const,
      label: hit.title,
      href: `/papers/${hit.id}`,
      score: hit.score,
    })),
  );

  const navEntries = $derived<Entry[]>(
    NAV.filter((item) => item.label.toLowerCase().includes(trimmed.toLowerCase())).map((item) => ({
      kind: 'nav' as const,
      label: item.label,
      href: item.href,
    })),
  );

  const searchEntry = $derived<Entry[]>(
    trimmed
      ? [
          {
            kind: 'search' as const,
            label: `Search all papers for "${trimmed}"`,
            href: searchUrl(trimmed),
          },
        ]
      : [],
  );

  // A question puts Scout first, a lookup puts papers first, and both are always present.
  // The paper hits and the search-all row stay adjacent either way, so a group heading is
  // never drawn twice for the same group.
  const entries = $derived<Entry[]>(
    intent === 'command'
      ? (askEntry ? [askEntry] : [])
      : intent === 'question'
        ? [...(askEntry ? [askEntry] : []), ...paperEntries, ...searchEntry, ...navEntries]
        : [...paperEntries, ...searchEntry, ...(askEntry ? [askEntry] : []), ...navEntries],
  );

  const search = debounce(async () => {
    const q = trimmed;
    if (!q) {
      papers = [];
      return;
    }
    const seq = sequencer.next();
    searching = true;
    try {
      const response = await fetch(`/api/papers?q=${encodeURIComponent(q)}&limit=6`);
      if (!response.ok || !sequencer.isCurrent(seq)) return;
      const payload = (await response.json()) as { items: PaperHit[] };
      if (!sequencer.isCurrent(seq)) return;
      papers = payload.items.map(({ id, title, score }) => ({
        id,
        title: stripMath(title),
        score,
      }));
    } catch {
      if (sequencer.isCurrent(seq)) papers = [];
    } finally {
      // Only the newest request may clear the flag; an older one finishing late would
      // otherwise report "done" while the current search is still running.
      if (sequencer.isCurrent(seq)) searching = false;
    }
  }, 250);

  $effect(() => {
    // Track the query; a command is not a paper search, so it clears rather than fetches.
    const q = trimmed;
    selected = 0;
    if (!q || intent === 'command') {
      search.cancel();
      sequencer.next(); // Retire any reply still in flight for an older query.
      papers = [];
      searching = false;
      return;
    }
    search.run();
    return () => search.cancel();
  });

  $effect(() => {
    // The dictionary is refetched whenever the panel opens: imports and stream enrichment
    // between opens should show up, and the read is a few milliseconds server-side.
    if (open) void loadDictionary();
  });

  async function loadDictionary() {
    try {
      const response = await fetch('/api/keywords');
      if (!response.ok) return;
      dictionary = (await response.json()).items;
    } catch {
      dictionary = null;
    }
  }

  function scrollToLatest() {
    if (body) body.scrollTop = body.scrollHeight;
  }

  function insertKeyword(keyword: string) {
    // Replace the partial word being typed with the chosen keyword.
    query = query.replace(/[a-z0-9]+$/i, '').trimEnd();
    query = query ? `${query} ${keyword} ` : `${keyword} `;
    inputEl?.focus();
  }

  function show() {
    open = true;
  }

  function hide() {
    open = false;
    selected = 0;
  }

  function run(entry: Entry) {
    if (entry.kind === 'ask') {
      if (busy) return;
      query = '';
      asked = true;
      void panel?.ask(entry.question, intent === 'command' ? 'llm' : 'fast');
    } else if (entry.kind === 'web') {
      if (busy) return;
      query = '';
      asked = true;
      void panel?.runWebSearch(entry.query);
    } else {
      window.location.href = entry.href;
    }
  }

  function onWindowKeydown(event: KeyboardEvent) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      inputEl?.focus();
      inputEl?.select();
      show();
    } else if (event.key === 'Escape' && open) {
      hide();
      inputEl?.blur();
    }
  }

  function onDocumentClick(event: MouseEvent) {
    const target = event.target as Element;
    if (target.closest('[data-open-omnibox]')) {
      event.preventDefault();
      inputEl?.focus();
      show();
      return;
    }
    if (open && root && !root.contains(target)) hide();
  }

  function onInputKeydown(event: KeyboardEvent) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (entries.length > 0) selected = (selected + 1) % entries.length;
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      if (entries.length > 0) selected = (selected - 1 + entries.length) % entries.length;
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const entry = entries[selected];
      if (entry) run(entry);
      else if (trimmed) window.location.href = searchUrl(trimmed);
    }
  }

  // A section heading is emitted whenever the group changes going down the flat list, so
  // the keyboard index and the visual grouping cannot drift apart.
  function heading(index: number): string | null {
    const kind = entries[index].kind;
    if (index === 0) return GROUP_LABEL[kind];
    return GROUP_LABEL[entries[index - 1].kind] === GROUP_LABEL[kind] ? null : GROUP_LABEL[kind];
  }
</script>

<svelte:window onkeydown={onWindowKeydown} />
<svelte:document onclick={onDocumentClick} />

<div class="omnibox" bind:this={root}>
  <div class="field" class:open>
    <Search size={16} aria-hidden="true" />
    <input
      bind:this={inputEl}
      bind:value={query}
      onfocus={show}
      onkeydown={onInputKeydown}
      type="text"
      placeholder="Search papers or ask Scout"
      aria-label="Search papers or ask Scout"
      role="combobox"
      aria-expanded={open}
      aria-controls="omnibox-panel"
      aria-activedescendant={open && entries.length > 0 ? `omnibox-option-${selected}` : undefined}
      aria-autocomplete="list"
    />
    {#if busy}
      <button class="stop" type="button" onclick={() => panel?.stop()} aria-label="Stop">
        <Square size={13} aria-hidden="true" />
      </button>
    {:else}
      <kbd aria-hidden="true">&#8984;K</kbd>
    {/if}
  </div>

  {#if open}
    <div class="panel" id="omnibox-panel">
      <div class="body" bind:this={body}>
        {#if suggestions.length > 0}
          <p class="chips" aria-label="Keyword suggestions">
            {#each suggestions as suggestion}
              <button type="button" class="chip" onclick={() => insertKeyword(suggestion.keyword)}>
                {suggestion.keyword}<span class="chipcount">{suggestion.papers}</span>
              </button>
            {/each}
          </p>
        {/if}

        {#if entries.length > 0}
          <ul class="entries" role="listbox" aria-label="Results">
            {#each entries as entry, index}
              {@const label = heading(index)}
              {#if label}
                <li class="group" role="presentation">{label}</li>
              {/if}
              <li
                id={`omnibox-option-${index}`}
                role="option"
                aria-selected={index === selected}
                class:active={index === selected}
              >
                <button
                  type="button"
                  tabindex="-1"
                  onclick={() => run(entry)}
                  onpointerenter={() => (selected = index)}
                >
                  {#if entry.kind === 'paper'}
                    <FileText size={14} aria-hidden="true" />
                  {:else if entry.kind === 'search'}
                    <Search size={14} aria-hidden="true" />
                  {:else if entry.kind === 'web'}
                    <Globe size={14} aria-hidden="true" />
                  {:else if entry.kind === 'ask'}
                    <Sparkles size={14} aria-hidden="true" />
                  {:else}
                    <CornerDownLeft size={14} aria-hidden="true" />
                  {/if}
                  <span class="label">{entry.label}</span>
                  {#if entry.kind === 'paper' && entry.score !== null}
                    <span class="score">{entry.score.toFixed(3)}</span>
                  {/if}
                </button>
              </li>
            {/each}
          </ul>
        {:else if trimmed && intent === 'command'}
          <p class="none">Unknown command.</p>
        {:else if trimmed && !searching}
          <p class="none">Nothing matches - keep typing.</p>
        {/if}

        {#if searching && papers.length === 0 && intent !== 'command'}
          <!-- Only while there is nothing to show. Once results exist they stay put through
               the next keystroke, so refining a query never flashes the list away. -->
          <div class="loading" aria-hidden="true">
            <span class="skeleton"></span>
            <span class="skeleton"></span>
            <span class="skeleton"></span>
          </div>
        {/if}

        <div class:hidden={!asked}>
          <ScoutPanel
            bind:this={panel}
            {dictionary}
            onbusy={(value) => (busy = value)}
            onactivity={scrollToLatest}
          />
        </div>

        {#if !asked && !trimmed}
          <div class="welcome">
            <ScoutMascot size={48} />
            <p>Type to find papers, or ask a question and Scout answers from what it has read.</p>
          </div>
        {/if}
      </div>

      <p class="foot">
        {#if hint}
          {hint}
        {:else if asked}
          Scout can make mistakes, double check responses.
        {:else}
          Try /web for a quick web search, or /ai to ask the model directly.
        {/if}
      </p>
    </div>
  {/if}
</div>

<style>
  .omnibox {
    position: relative;
    flex: 1;
    min-width: 0;
    max-width: 44rem;
  }
  /* One of the three controls that keeps a border: this is a text field, and a field with
     no edge is not obviously typeable. */
  .field {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.4rem 0.5rem 0.4rem 0.8rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-full, 999px);
    background: var(--surface, #fff);
    color: var(--muted, #5d6570);
    transition:
      border-color var(--dur-fast, 0.15s) var(--ease-out, ease),
      box-shadow var(--dur-fast, 0.15s) var(--ease-out, ease);
  }
  .field:hover {
    border-color: var(--line-strong, #d1d6dc);
  }
  .field:focus-within,
  .field.open {
    border-color: var(--accent, #c2410c);
    box-shadow: 0 0 0 3px var(--accent-soft, #fef3c7);
  }
  .field input {
    flex: 1;
    min-width: 0;
    border: none;
    outline: none;
    background: none;
    color: var(--ink, #17191c);
    font: inherit;
    font-size: var(--text-sm, 0.875rem);
    line-height: 1.6;
  }
  .field input::placeholder {
    color: var(--muted, #5d6570);
  }
  kbd {
    flex-shrink: 0;
    padding: 0.05rem 0.4rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: 6px;
    background: var(--surface-2, #f4f0e8);
    color: var(--muted, #5d6570);
    font-family: inherit;
    font-size: 0.7rem;
  }
  .stop {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.55rem;
    height: 1.55rem;
    border: none;
    border-radius: 999px;
    background: var(--ink, #17191c);
    color: var(--surface, #fff);
    cursor: pointer;
  }
  .stop:hover {
    background: var(--muted, #5d6570);
  }

  /* Anchored to the field's right edge, never its left: that is what keeps a wide panel
     clear of the navigation rail on a middling window without measuring anything. */
  .panel {
    position: absolute;
    top: calc(100% + 0.5rem);
    right: 0;
    z-index: 5;
    width: min(45rem, calc(100vw - 2rem));
    max-height: 70vh;
    display: flex;
    flex-direction: column;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-md, 14px);
    background: var(--surface, #fff);
    box-shadow: var(--shadow-lg, 0 16px 48px rgb(23 25 28 / 0.16));
    overflow: hidden;
    animation: panel-in var(--dur-fast, 0.15s) var(--ease-out, ease);
  }
  @keyframes panel-in {
    from {
      opacity: 0;
      transform: translateY(-4px);
    }
  }
  .body {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
  }
  .hidden {
    display: none;
  }

  .entries {
    list-style: none;
    margin: 0;
    padding: 0.4rem;
  }
  .group {
    padding: 0.5rem 0.65rem 0.25rem;
    color: var(--muted, #5d6570);
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .entries button {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    width: 100%;
    padding: 0.5rem 0.65rem;
    border: none;
    border-radius: 8px;
    background: none;
    color: var(--ink, #17191c);
    font: inherit;
    font-size: 0.9rem;
    text-align: left;
    cursor: pointer;
  }
  .entries li.active button {
    background: var(--surface-2, #f4f0e8);
  }
  .entries button :global(svg) {
    flex-shrink: 0;
    color: var(--muted, #5d6570);
  }
  .label {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .score {
    flex-shrink: 0;
    padding: 0.05rem 0.5rem;
    border-radius: 999px;
    background: var(--accent-soft, #fef3c7);
    color: var(--accent-ink, #78350f);
    font-size: 0.72rem;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
  }
  .none {
    margin: 0;
    padding: 0.9rem 1rem;
    color: var(--muted, #5d6570);
    font-size: 0.88rem;
  }
  .loading {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    padding: 0.5rem 1.05rem 0.75rem;
  }
  .loading .skeleton {
    height: 0.95rem;
  }
  .loading .skeleton:nth-child(2) {
    width: 82%;
  }
  .loading .skeleton:nth-child(3) {
    width: 64%;
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
    margin: 0;
    padding: 0.6rem 0.75rem 0.1rem;
  }
  .chip {
    display: inline-flex;
    align-items: baseline;
    gap: 0.3rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: 999px;
    background: var(--surface, #fff);
    color: var(--ink, #17191c);
    font: inherit;
    font-size: 0.75rem;
    font-weight: 500;
    padding: 0.15rem 0.6rem;
    cursor: pointer;
    transition: background-color var(--dur-fast, 0.15s) var(--ease-out, ease);
  }
  .chip:hover {
    background: var(--surface-2, #f4f0e8);
  }
  .chipcount {
    font-size: 0.68rem;
    color: var(--muted, #5d6570);
  }

  .welcome {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.6rem;
    padding: 1.75rem 2rem 2rem;
    text-align: center;
  }
  .welcome p {
    margin: 0;
    max-width: 26rem;
    color: var(--muted, #5d6570);
    font-size: 0.88rem;
  }

  .foot {
    margin: 0;
    padding: 0.5rem 1rem;
    border-top: 1px solid var(--line, #e6e1d5);
    color: var(--muted, #5d6570);
    font-size: 0.72rem;
  }

  @media (prefers-reduced-motion: reduce) {
    .panel {
      animation: none;
    }
  }
  @media (max-width: 52rem) {
    .omnibox {
      max-width: none;
    }
    kbd {
      display: none;
    }
  }
</style>
