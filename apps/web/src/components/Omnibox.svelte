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

  import {
    CornerDownLeft,
    FileText,
    Globe,
    History,
    Search,
    Sparkles,
    Square,
  } from 'lucide-svelte';

  import { navigate } from 'astro:transitions/client';

  import {
    ask as askScout,
    chat,
    clearConversation,
    runWebSearch,
    stopStreaming,
  } from '../lib/chat-state.svelte';
  import type { KeywordCount } from '../lib/chat-types';
  import { commandHint, parseInput } from '../lib/commands';
  import { matchKeywords } from '../lib/keyword-match';
  import { stripMath } from '../lib/math-text';
  import { prefersReducedMotion } from '../lib/motion';
  import { classify, createSequencer, debounce, searchUrl } from '../lib/omnibox';
  import { clickedOutside, lockBodyScroll } from '../lib/overlay';
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
    | { kind: 'recent'; label: string; href: string }
    | { kind: 'opened'; label: string; href: string }
    | { kind: 'nav'; label: string; href: string };

  // Home only. Every other destination is one glance away in the navigation rail, and a
  // panel that repeats them buries the papers it exists to show.
  const NAV: { href: string; label: string }[] = [{ href: '/', label: 'Home' }];

  const GROUP_LABEL: Record<Entry['kind'], string> = {
    ask: 'Scout',
    web: 'Scout',
    paper: 'Papers',
    search: 'Papers',
    recent: 'Recent searches',
  opened: 'Recently opened',
    nav: 'Jump to',
  };

  let open = $state(false);
  let query = $state('');
  let papers = $state<PaperHit[]>([]);
  let selected = $state(0);
  let searching = $state(false);
  let dictionary = $state<KeywordCount[] | null>(null);
  // Phrases this account searched for before, and papers it opened. Signed-in only, and both
  // requests simply 401 otherwise, so nothing here needs to know whether anybody is signed in.
  let history = $state<string[]>([]);
  let recent = $state<{ id: string; title: string }[]>([]);

  let root: HTMLElement | undefined = $state();
  let inputEl: HTMLInputElement | undefined = $state();
  let body: HTMLElement | undefined = $state();

  const sequencer = createSequencer();

  const trimmed = $derived(query.trim());
  const intent = $derived(classify(query));
  const hint = $derived(commandHint(query));
  const suggestions = $derived(
    !chat.busy && dictionary && intent !== 'command' && trimmed.length >= 2
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

  // Offered only on an empty field. Once there is something typed, matching papers are a
  // better answer than what was typed last week, and a list that keeps reordering under the
  // cursor is worse than no list.
  const recentEntries = $derived<Entry[]>(
    trimmed
      ? []
      : history.slice(0, 5).map((phrase) => ({
          kind: 'recent' as const,
          label: phrase,
          href: searchUrl(phrase),
        })),
  );

  // Papers, not phrases: the two answer different questions on an empty field - "what was I
  // looking for" and "what was I reading" - and getting back to a paper you had open is the
  // one people ask for by name.
  const openedEntries = $derived<Entry[]>(
    trimmed
      ? []
      : recent.slice(0, 5).map((paper) => ({
          kind: 'opened' as const,
          label: paper.title,
          href: `/papers/${paper.id}`,
        })),
  );

  // A question puts Scout first, a lookup puts papers first, and both are always present.
  // The paper hits and the search-all row stay adjacent either way, so a group heading is
  // never drawn twice for the same group.
  const entries = $derived<Entry[]>(
    intent === 'command'
      ? (askEntry ? [askEntry] : [])
      : intent === 'question'
        ? [...(askEntry ? [askEntry] : []), ...paperEntries, ...searchEntry, ...navEntries]
        : [
            ...paperEntries,
            ...searchEntry,
            ...(askEntry ? [askEntry] : []),
            ...recentEntries,
            ...openedEntries,
            ...navEntries,
          ],
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
    // The list can shrink under a hovered index - results refining, a command typed - and
    // an out-of-range selection would point aria-activedescendant at nothing and make
    // Enter fall through to a full search.
    if (selected >= entries.length) selected = entries.length > 0 ? entries.length - 1 : 0;
  });

  $effect(() => {
    // Both are refetched whenever the panel opens: imports and stream enrichment between opens
    // should show up, searches made in another tab likewise, and each read is a few
    // milliseconds server-side.
    if (open) {
      void loadDictionary();
      void loadHistory();
      void loadRecent();
    }
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

  /** A 401 here means signed out, which is not an error - it means there is no history. */
  async function loadHistory() {
    try {
      const response = await fetch('/api/me/history');
      if (!response.ok) {
        history = [];
        return;
      }
      history = (await response.json()).items ?? [];
    } catch {
      history = [];
    }
  }

  /** A 401 here means signed out, which is not an error - it means nothing was opened. */
  async function loadRecent() {
    try {
      const response = await fetch('/api/me/recent');
      if (!response.ok) {
        recent = [];
        return;
      }
      const body = (await response.json()) as { items: { id: string; title: string }[] };
      recent = (body.items ?? []).map((paper) => ({
        id: paper.id,
        title: stripMath(paper.title),
      }));
    } catch {
      recent = [];
    }
  }

  /**
   * Remember a phrase that was actually searched for.
   *
   * Fired on the way out, so the list reflects searches rather than keystrokes. Not awaited:
   * the navigation is already happening and a cache write must not be in front of it.
   */
  function rememberSearch(phrase: string) {
    void fetch('/api/me/history', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: phrase }),
      keepalive: true,
    }).catch(() => undefined);
  }

  // The body shows one thing at a time: the suggestion list while typing, the conversation
  // once one exists, the welcome otherwise. Stacked, the list pushed the transcript out of
  // view and the auto-scroll jumped past whichever the reader wanted.
  const listShown = $derived(Boolean(trimmed) || !chat.asked);

  function scrollToLatest() {
    if (body) body.scrollTop = body.scrollHeight;
  }

  // Streaming only follows a reader who is already at the end; scrolling up to reread must
  // not be fought by every arriving token. Sending a question still forces the jump.
  function followLatest() {
    if (!body) return;
    if (body.scrollHeight - body.scrollTop - body.clientHeight < 80) scrollToLatest();
  }

  function insertKeyword(keyword: string) {
    // Replace the partial word being typed with the chosen keyword.
    query = query.replace(/[a-z0-9]+$/i, '').trimEnd();
    query = query ? `${query} ${keyword} ` : `${keyword} `;
    inputEl?.focus();
  }

  let closing = $state(false);
  let closeTimer: ReturnType<typeof setTimeout> | null = null;

  // On a phone the panel covers the page, so the page must not keep scrolling under it -
  // the same discipline as the rail drawer. Wider tiers keep the dropdown feel, where
  // background scroll is expected. The effect cleanup releases the lock on close, on
  // navigation, and on destroy alike.
  $effect(() => {
    if (!open) return;
    if (!window.matchMedia('(max-width: 40rem)').matches) return;
    const unlock = lockBodyScroll();
    return () => unlock();
  });

  function show() {
    if (closeTimer) {
      clearTimeout(closeTimer);
      closeTimer = null;
    }
    closing = false;
    open = true;
    // A reopened conversation lands on its latest exchange; the gentle follow alone would
    // leave a fresh mount at the top.
    requestAnimationFrame(scrollToLatest);
  }

  function hide() {
    if (!open || closeTimer) return;
    // The panel animates in, so it leaves the same way - a beat of fade rather than a cut.
    // Reduced motion skips straight to closed, and show() cancels a close in flight.
    if (prefersReducedMotion()) {
      open = false;
      selected = 0;
      return;
    }
    closing = true;
    closeTimer = setTimeout(() => {
      closeTimer = null;
      closing = false;
      open = false;
      selected = 0;
    }, 130);
  }

  function run(entry: Entry) {
    if (entry.kind === 'ask') {
      if (chat.busy) return;
      query = '';
      rememberSearch(entry.question);
      void askScout(entry.question, intent === 'command' ? 'llm' : 'fast', dictionary);
      // The thread replaces the list on the next frame; land on the fresh question even if
      // the reader had scrolled up through the transcript.
      requestAnimationFrame(scrollToLatest);
    } else if (entry.kind === 'web') {
      if (chat.busy) return;
      query = '';
      rememberSearch(entry.query);
      void runWebSearch(entry.query);
      requestAnimationFrame(scrollToLatest);
    } else {
      // Searches and questions are remembered (the two branches above record theirs);
      // opening one paper is a click, and the reading history covers it. The navigation
      // goes through the client router so the persisted field keeps its state.
      if (entry.kind === 'search' && trimmed) rememberSearch(trimmed);
      hide();
      void navigate(entry.href);
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
    // Outside-ness is judged on the composed path, snapshotted at dispatch: by the time
    // the click bubbles here, choosing an entry has already cleared the query and Svelte
    // may have re-rendered the row away, so containment-at-handler-time would read a
    // detached node and close the panel in answer to its own Ask row.
    if (open && clickedOutside(event, root)) hide();
  }

  function onInputKeydown(event: KeyboardEvent) {
    // While the thread is showing the list is not rendered, so the keyboard must not act
    // on its hidden rows - Enter over the transcript firing a stale recent would be worse
    // than doing nothing.
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (listShown && entries.length > 0) selected = (selected + 1) % entries.length;
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      if (listShown && entries.length > 0) selected = (selected - 1 + entries.length) % entries.length;
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const entry = listShown ? entries[selected] : undefined;
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
    <!-- Scout's own mark, in the field rather than only inside the panel's welcome state -
         which was the one place it appeared, so anyone who typed before opening the panel
         never saw it at all. The magnifier moves to the far end: the placeholder carries the
         search affordance, and the owl says whose field this is. -->
    <ScoutMascot size={20} />
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
    {#if chat.busy}
      <button class="stop" type="button" onclick={stopStreaming} aria-label="Stop">
        <Square size={13} aria-hidden="true" />
      </button>
    {:else}
      <Search size={15} aria-hidden="true" class="mag" />
      <kbd aria-hidden="true">&#8984;K</kbd>
    {/if}
  </div>

  {#if open}
    <div class="panel" class:closing id="omnibox-panel">
      <div class="body" bind:this={body}>
        {#if trimmed && suggestions.length > 0}
          <p class="chips" aria-label="Keyword suggestions">
            {#each suggestions as suggestion}
              <button type="button" class="chip" onclick={() => insertKeyword(suggestion.keyword)}>
                {suggestion.keyword}<span class="chipcount">{suggestion.papers}</span>
              </button>
            {/each}
          </p>
        {/if}

        {#if listShown && entries.length > 0}
          <ul class="entries" role="listbox" aria-label="Results">
            {#each entries as entry, index}
              {@const label = heading(index)}
              {#if label}
                <li class="group" role="presentation">{label}</li>
              {/if}
              <!-- The option is the row itself rather than a button inside it. ARIA forbids
                   interactive descendants of an option, and none is needed: this is a combobox,
                   so the keyboard lives on the input and reaches here through
                   aria-activedescendant. Hence no key handler on the row. -->
              <!-- svelte-ignore a11y_click_events_have_key_events -->
              <li
                id={`omnibox-option-${index}`}
                role="option"
                aria-selected={index === selected}
                class:active={index === selected}
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
                {:else if entry.kind === 'recent'}
                  <History size={14} aria-hidden="true" />
                {:else if entry.kind === 'opened'}
                  <FileText size={14} aria-hidden="true" />
                {:else}
                  <CornerDownLeft size={14} aria-hidden="true" />
                {/if}
                <span class="label">{entry.label}</span>
                {#if entry.kind === 'paper' && entry.score !== null}
                  <span class="score">{entry.score.toFixed(3)}</span>
                {/if}
              </li>
            {/each}
          </ul>
        {:else if trimmed && intent === 'command'}
          <p class="none">Unknown command.</p>
        {:else if trimmed && !searching}
          <p class="none">Nothing matches - keep typing.</p>
        {/if}

        {#if trimmed && searching && papers.length === 0 && intent !== 'command'}
          <!-- Only while there is nothing to show. Once results exist they stay put through
               the next keystroke, so refining a query never flashes the list away. -->
          <div class="loading" aria-hidden="true">
            <span class="skeleton"></span>
            <span class="skeleton"></span>
            <span class="skeleton"></span>
          </div>
        {/if}

        {#if !trimmed && chat.asked}
          <ScoutPanel onactivity={followLatest} />
        {:else if !trimmed && !chat.asked}
          <div class="welcome">
            <ScoutMascot size={48} />
            <p>Type to find papers, or ask a question and Scout answers from what it has read.</p>
          </div>
        {/if}
      </div>

      <p class="foot">
        <span class="footnote">
          {#if hint}
            {hint}
          {:else if chat.asked}
            Scout can make mistakes, double check responses.
          {:else}
            Try /web for a quick web search, or /ai to ask the model directly.
          {/if}
        </span>
        {#if chat.asked && chat.messages.length > 0 && !trimmed}
          <!-- The transcript survives closing and reloading for a day, so forgetting it has
               to be a button rather than an accident; it lives here so the thread starts
               with the conversation, not a control. -->
          <button type="button" class="clear" onclick={clearConversation}>Clear</button>
        {/if}
      </p>
    </div>
  {/if}
</div>

<style>
  /* Fills the header's middle column, which is what centres it; the column carries the cap. */
  .omnibox {
    position: relative;
    width: 100%;
    min-width: 0;
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
  /* The owl and the magnifier both hold their size when the field is squeezed. */
  .field > :global(svg) {
    flex-shrink: 0;
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
     clear of the navigation rail on a middling window without measuring anything. dvh, not
     vh: under a phone URL bar the two differ by the bar's height. */
  .panel {
    position: absolute;
    top: calc(100% + 0.5rem);
    right: 0;
    z-index: 5;
    width: min(45rem, calc(100vw - 2rem));
    max-height: 70dvh;
    display: flex;
    flex-direction: column;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-md, 14px);
    background: var(--surface, #fff);
    box-shadow: var(--shadow-lg, 0 16px 48px rgb(23 25 28 / 0.16));
    overflow: hidden;
    animation: panel-in var(--dur-fast, 0.15s) var(--ease-out, ease);
  }
  /* Phone: right-anchored to a field that sits mid-header, the dropdown ran off the left
     edge of the screen. Fixed instead - the header's backdrop-filter makes it the
     containing block for fixed descendants, and the header spans the viewport, so these
     edge offsets are viewport offsets. Height comes from the visible viewport, not the
     field. */
  @media (max-width: 40rem) {
    .panel {
      position: fixed;
      top: calc(var(--nav-height, 3.75rem) + 0.4rem);
      left: 0.4rem;
      right: 0.4rem;
      width: auto;
      max-height: calc(100dvh - var(--nav-height, 3.75rem) - 1rem);
    }
  }
  @keyframes panel-in {
    from {
      opacity: 0;
      transform: translateY(-4px);
    }
  }
  .panel.closing {
    animation: panel-out var(--dur-fast, 0.15s) var(--ease-out, ease) forwards;
  }
  @keyframes panel-out {
    to {
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
  .entries li[role='option'] {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    width: 100%;
    padding: 0.5rem 0.65rem;
    border-radius: 8px;
    color: var(--ink, #17191c);
    font-size: 0.9rem;
    text-align: left;
    cursor: pointer;
  }
  .entries li.active {
    background: var(--surface-2, #f4f0e8);
  }
  .entries li[role='option'] :global(svg) {
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
    padding: 1.5rem 1rem 1.75rem;
    text-align: center;
  }
  .welcome p {
    margin: 0;
    max-width: 26rem;
    color: var(--muted, #5d6570);
    font-size: var(--text-sm, 0.875rem);
  }

  .foot {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin: 0;
    padding: 0.5rem 1rem;
    border-top: 1px solid var(--line, #e6e1d5);
    color: var(--muted, #5d6570);
    font-size: 0.72rem;
  }
  .footnote {
    flex: 1;
    min-width: 0;
  }
  .clear {
    flex-shrink: 0;
    border: none;
    background: none;
    padding: 0.2rem 0.3rem;
    color: var(--muted, #5d6570);
    font: inherit;
    cursor: pointer;
    transition: color var(--dur-fast, 0.15s) var(--ease-out, ease);
  }
  .clear:hover {
    color: var(--ink, #17191c);
    text-decoration: underline;
  }

  @media (prefers-reduced-motion: reduce) {
    .panel {
      animation: none;
    }
  }
  @media (max-width: 52rem) {
    kbd {
      display: none;
    }
  }
</style>
