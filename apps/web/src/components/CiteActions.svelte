<script lang="ts">
  // Citation tools for the paper detail page. The BibTeX string is generated
  // server-side and passed in, and lives inside a native <details>, so it stays
  // readable with JavaScript disabled; the clipboard buttons are the enhancement.

  import { Copy, Link, Quote } from 'lucide-svelte';

  let { bibtex, url }: { bibtex: string; url: string } = $props();

  let toast = $state('');
  let timer: ReturnType<typeof setTimeout> | undefined;

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast = `${what} copied`;
    } catch {
      toast = 'Copy failed — select the text instead';
    }
    clearTimeout(timer);
    timer = setTimeout(() => (toast = ''), 1500);
  }
</script>

<details class="cite">
  <summary>
    <Quote size={14} aria-hidden="true" />
    Cite
  </summary>
  <div class="panel">
    <pre>{bibtex}</pre>
    <div class="actions">
      <button onclick={() => copy(bibtex, 'BibTeX')}>
        <Copy size={14} aria-hidden="true" />
        Copy BibTeX
      </button>
      <button onclick={() => copy(url, 'Link')}>
        <Link size={14} aria-hidden="true" />
        Copy link
      </button>
      {#if toast}
        <span class="toast" role="status">{toast}</span>
      {/if}
    </div>
  </div>
</details>

<style>
  .cite {
    margin: 0;
  }
  summary {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.5rem 1.1rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: 999px;
    background: var(--surface, #fff);
    color: var(--ink, #17191c);
    font-size: 0.875rem;
    font-weight: 550;
    line-height: 1.3;
    cursor: pointer;
    list-style: none;
    transition:
      background-color 0.15s ease,
      border-color 0.15s ease;
  }
  summary::-webkit-details-marker {
    display: none;
  }
  summary:hover {
    background: var(--surface-2, #f4f0e8);
    border-color: var(--line-strong, #d1d6dc);
  }
  summary:focus-visible {
    outline: 2px solid var(--accent, #0f62fe);
    outline-offset: 2px;
  }
  .panel {
    margin-top: 0.75rem;
    max-width: 46rem;
  }
  pre {
    margin: 0;
    padding: 0.9rem 1rem;
    border-radius: var(--radius-sm, 10px);
    background: var(--surface-2, #f4f0e8);
    color: var(--ink, #17191c);
    font-size: 0.8rem;
    line-height: 1.55;
    overflow-x: auto;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    margin-top: 0.6rem;
  }
  .actions button {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.4rem 0.9rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: 999px;
    background: var(--surface, #fff);
    color: var(--ink, #17191c);
    font: inherit;
    font-size: 0.82rem;
    font-weight: 550;
    cursor: pointer;
    transition:
      background-color 0.15s ease,
      border-color 0.15s ease;
  }
  .actions button:hover {
    background: var(--surface-2, #f4f0e8);
    border-color: var(--line-strong, #d1d6dc);
  }
  .actions button:focus-visible {
    outline: 2px solid var(--accent, #0f62fe);
    outline-offset: 2px;
  }
  .toast {
    color: var(--muted, #5d6570);
    font-size: 0.8rem;
  }
  @media (prefers-reduced-motion: reduce) {
    summary,
    .actions button {
      transition: none;
    }
  }
</style>
