<script lang="ts">
  // One web search result with its import state machine. state is the imports-map value
  // for this hit: undefined (idle), 'busy', 'error', or the created paper id.
  import type { WebHit } from '../lib/chat-types';

  let {
    hit,
    state = undefined,
    onimport,
  }: { hit: WebHit; state?: string; onimport: (hit: WebHit) => void } = $props();
</script>

<div class="webhit">
  <p class="webtitle">{hit.title}{#if hit.year}&nbsp;({hit.year}){/if}</p>
  {#if hit.authors.length > 0}<p class="webmeta">{hit.authors.join(', ')}</p>{/if}
  {#if hit.snippet}<p class="websnippet">{hit.snippet}</p>{/if}
  <p class="webactions">
    <span class="provider">{hit.provider}</span>
    {#if hit.already_known && hit.paper_id}
      <a href={`/papers/${hit.paper_id}`}>In library - open</a>
    {:else if hit.arxiv_id}
      {#if state === 'busy'}
        <span class="webnote">Adding</span>
      {:else if state === 'error'}
        <span class="weberror">Could not add - try again</span>
      {:else if state}
        <a href={`/papers/${state}`}>Added to Reading list - open</a>
      {:else}
        <button class="ghost" onclick={() => onimport(hit)}>Add to library</button>
      {/if}
    {:else if hit.url}
      <a href={hit.url} target="_blank" rel="noreferrer">View source</a>
    {/if}
  </p>
</div>

<style>
  /* Same card shape and type scale as the fast-answer cards in ChatMessage: one border,
     one radius token, one title size, so the two kinds of result read as siblings. */
  .webhit {
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-sm, 10px);
    padding: 0.65rem 0.8rem;
    background: var(--surface, #fff);
  }
  .webhit p {
    margin: 0;
    padding: 0;
  }
  .webtitle {
    font-size: var(--text-sm, 0.875rem);
    font-weight: 600;
    color: var(--ink, #17191c);
  }
  .webmeta {
    font-size: var(--text-xs, 0.75rem);
    color: var(--muted, #5d6570);
  }
  .websnippet {
    font-size: 0.8rem;
    margin-top: 0.25rem;
    color: var(--ink, #17191c);
  }
  .webactions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.4rem;
    font-size: 0.8rem;
  }
  .webactions a {
    color: var(--accent-ink, #78350f);
    font-weight: 500;
  }
  .provider {
    font-size: var(--text-xs, 0.75rem);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted, #5d6570);
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-full, 999px);
    padding: 0.05rem 0.45rem;
  }
  .webnote {
    font-size: 0.8rem;
    color: var(--muted, #5d6570);
  }
  .weberror {
    font-size: 0.8rem;
    color: var(--danger-ink, #7f1d1d);
  }
  .ghost {
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-full, 999px);
    background: var(--surface, #fff);
    color: var(--ink, #17191c);
    font: inherit;
    font-size: 0.8rem;
    font-weight: 500;
    padding: 0.35rem 0.85rem;
    cursor: pointer;
    transition: background-color var(--dur-fast, 0.15s) var(--ease-out, cubic-bezier(0.2, 0, 0, 1));
  }
  .ghost:hover:not(:disabled) {
    background: var(--surface-2, #f4f0e8);
  }
  .ghost:focus-visible {
    outline: 2px solid var(--accent, #c2410c);
    outline-offset: 2px;
  }
</style>
