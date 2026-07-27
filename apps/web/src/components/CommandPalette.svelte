<script lang="ts">
  // Command palette: Cmd+K / Ctrl+K (or the header search button, matched via its
  // data-open-palette attribute) opens a modal combining static nav commands with
  // live paper results from the hybrid search, debounced through the same-origin
  // proxy.

  import { CornerDownLeft, FileText, Search } from 'lucide-svelte';

  import { stripMath } from '../lib/math-text';

  interface PaperHit {
    id: string;
    title: string;
    score: number | null;
  }

  interface Entry {
    href: string;
    label: string;
    kind: 'nav' | 'paper' | 'search';
    score?: number | null;
  }

  const NAV: { href: string; label: string }[] = [
    { href: '/', label: 'Home' },
    { href: '/digests', label: 'Digests' },
    { href: '/saved', label: 'Saved' },
    { href: '/profile', label: 'Profile' },
  ];

  let open = $state(false);
  let query = $state('');
  let papers = $state<PaperHit[]>([]);
  let selected = $state(0);
  let input = $state<HTMLInputElement | undefined>();
  let previousFocus: Element | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let requestSeq = 0;

  const commands = $derived(NAV.filter((item) => matches(item.label, query)));
  // With a query, a pinned first row hands the search to the feed page for the
  // full result list; Enter with nothing highlighted lands there too.
  const searchAll = $derived<Entry[]>(
    query.trim()
      ? [
          {
            href: `/?q=${encodeURIComponent(query.trim())}`,
            label: `Search all papers for "${query.trim()}"`,
            kind: 'search' as const,
          },
        ]
      : [],
  );
  const entries = $derived<Entry[]>([
    ...searchAll,
    ...commands.map((item) => ({ href: item.href, label: item.label, kind: 'nav' as const })),
    ...papers.map((hit) => ({
      href: `/papers/${hit.id}`,
      label: hit.title,
      kind: 'paper' as const,
      score: hit.score,
    })),
  ]);

  function matches(label: string, needle: string): boolean {
    return label.toLowerCase().includes(needle.trim().toLowerCase());
  }

  function show() {
    previousFocus = document.activeElement;
    open = true;
    query = '';
    papers = [];
    selected = 0;
  }

  function hide() {
    open = false;
    if (previousFocus instanceof HTMLElement) previousFocus.focus();
  }

  $effect(() => {
    if (open) input?.focus();
  });

  $effect(() => {
    // Debounced paper search: track the query, wait 250ms, keep only the newest reply.
    const q = query.trim();
    clearTimeout(timer);
    selected = 0;
    if (!open || !q) {
      papers = [];
      return;
    }
    timer = setTimeout(async () => {
      const seq = ++requestSeq;
      try {
        const response = await fetch(`/api/papers?q=${encodeURIComponent(q)}&limit=6`);
        if (!response.ok || seq !== requestSeq) return;
        const body = (await response.json()) as { items: PaperHit[] };
        if (seq === requestSeq) {
          papers = body.items.map(({ id, title, score }) => ({
            id,
            title: stripMath(title),
            score,
          }));
          selected = 0;
        }
      } catch {
        if (seq === requestSeq) papers = [];
      }
    }, 250);
  });

  function onWindowKeydown(event: KeyboardEvent) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (open) hide();
      else show();
    } else if (event.key === 'Escape' && open) {
      hide();
    }
  }

  function onDocumentClick(event: MouseEvent) {
    if ((event.target as Element).closest('[data-open-palette]')) {
      event.preventDefault();
      show();
    }
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
      const q = query.trim();
      if (entry) window.location.href = entry.href;
      else if (q) window.location.href = `/?q=${encodeURIComponent(q)}`;
    }
  }
</script>

<svelte:window onkeydown={onWindowKeydown} />
<svelte:document onclick={onDocumentClick} />

{#if open}
  <div
    class="backdrop"
    onclick={(event) => {
      if (event.target === event.currentTarget) hide();
    }}
    role="presentation"
  >
    <div class="palette" role="dialog" aria-modal="true" aria-label="Command palette">
      <div class="field">
        <Search size={16} aria-hidden="true" />
        <input
          bind:this={input}
          bind:value={query}
          onkeydown={onInputKeydown}
          type="text"
          placeholder="Search papers or jump to a page…"
          role="combobox"
          aria-expanded={entries.length > 0}
          aria-controls="palette-results"
          aria-activedescendant={entries.length > 0 ? `palette-option-${selected}` : undefined}
          aria-autocomplete="list"
        />
        <kbd>esc</kbd>
      </div>
      <ul class="results" id="palette-results" role="listbox" aria-label="Results">
        {#each entries as entry, index}
          <li
            id={`palette-option-${index}`}
            role="option"
            aria-selected={index === selected}
            class:active={index === selected}
          >
            <a
              href={entry.href}
              tabindex="-1"
              onpointerenter={() => (selected = index)}
            >
              {#if entry.kind === 'paper'}
                <FileText size={14} aria-hidden="true" />
              {:else if entry.kind === 'search'}
                <Search size={14} aria-hidden="true" />
              {:else}
                <CornerDownLeft size={14} aria-hidden="true" />
              {/if}
              <span class="label">{entry.label}</span>
              {#if entry.kind === 'paper' && entry.score !== null && entry.score !== undefined}
                <span class="score">{entry.score.toFixed(3)}</span>
              {/if}
            </a>
          </li>
        {/each}
        {#if entries.length === 0}
          <li class="none" role="presentation">Nothing matches — keep typing.</li>
        {/if}
      </ul>
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 40;
    background: rgb(0 0 0 / 0.32);
    display: flex;
    justify-content: center;
    align-items: flex-start;
    padding: 12vh var(--gutter, 1.25rem) 2rem;
  }
  .palette {
    width: min(560px, 100%);
    display: flex;
    flex-direction: column;
    max-height: 60vh;
    background: var(--surface, #fff);
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-md, 14px);
    box-shadow: var(--shadow-md, 0 12px 32px rgb(0 0 0 / 0.2));
    overflow: hidden;
  }
  .field {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.8rem 1rem;
    border-bottom: 1px solid var(--line, #e6e1d5);
    color: var(--muted, #5d6570);
  }
  .field input {
    flex: 1;
    min-width: 0;
    border: none;
    outline: none;
    background: none;
    color: var(--ink, #17191c);
    font: inherit;
    font-size: 0.95rem;
  }
  .field input::placeholder {
    color: var(--muted, #5d6570);
  }
  kbd {
    padding: 0.1rem 0.4rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: 6px;
    background: var(--surface-2, #f4f0e8);
    color: var(--muted, #5d6570);
    font-family: inherit;
    font-size: 0.7rem;
  }
  .results {
    list-style: none;
    margin: 0;
    padding: 0.4rem;
    overflow-y: auto;
  }
  .results a {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.55rem 0.65rem;
    border-radius: 8px;
    color: var(--ink, #17191c);
    font-size: 0.9rem;
    text-decoration: none;
  }
  .results li.active a {
    background: var(--surface-2, #f4f0e8);
  }
  .results a :global(svg) {
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
    padding: 0.8rem 0.65rem;
    color: var(--muted, #5d6570);
    font-size: 0.88rem;
  }
</style>
