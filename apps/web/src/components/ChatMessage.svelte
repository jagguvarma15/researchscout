<script lang="ts">
  // One chat exchange entry: Scout's avatar plus whichever body the message state calls
  // for - the phase loader, fast-mode result cards, formatted LLM prose, or a plain
  // bubble - followed by citations, actions, and the web-search fallback. All state
  // lives in the parent; this component only renders and forwards intents.
  import type { Message, WebHit } from '../lib/chat-types';
  import { formatMonthYear, renderAnswerHtml } from '../lib/chat-format';
  import ScoutMascot from './ScoutMascot.svelte';
  import WebHitCard from './WebHitCard.svelte';

  let {
    message,
    busy,
    last,
    onsummarize,
    onwebsearch,
    onimport,
  }: {
    message: Message;
    busy: boolean;
    last: boolean;
    onsummarize: () => void;
    onwebsearch: () => void;
    onimport: (hit: WebHit) => void;
  } = $props();

  function statusLabel(current: Message): string | null {
    if (current.role !== 'assistant' || current.text || current.results) return null;
    if (current.phase === 'searching') {
      if (current.matched && current.matched.keywords.length > 0) {
        const plural = current.matched.papers === 1 ? 'paper' : 'papers';
        return `Matching: ${current.matched.keywords.join(', ')} across ${current.matched.papers} ${plural}`;
      }
      return 'Searching papers';
    }
    if (current.phase === 'thinking') {
      if (current.retrieved && current.retrieved > 0) {
        return `Reading ${current.retrieved} paper${current.retrieved === 1 ? '' : 's'}`;
      }
      return 'Thinking';
    }
    return null;
  }

  const status = $derived(statusLabel(message));
  const streamingCaret = $derived(message.role === 'assistant' && busy && last);
  // Completed LLM answers render as formatted HTML; renderAnswerHtml escapes everything
  // and linkifies only the server-confirmed used ids, which is what makes @html safe.
  const formatted = $derived(
    message.role === 'assistant' &&
      message.mode === 'llm' &&
      message.phase === 'done' &&
      !message.error &&
      !message.stopped &&
      message.text
      ? renderAnswerHtml(message.text, new Set((message.used ?? []).map((paper) => paper.id)))
      : null,
  );
</script>

<div class="msg {message.role}" class:error={message.error}>
  {#if message.role === 'assistant'}
    <span class="avatar" aria-hidden="true"><ScoutMascot size={18} /></span>
  {/if}
  <div class="content">
    {#if status}
      <p class="bubble pending" role="status">
        {status}<span class="dots" aria-hidden="true"
          ><span>.</span><span>.</span><span>.</span></span
        >
      </p>
    {:else if message.results}
      <div class="cards">
        <p class="lead">
          Found {message.results.length} matching paper{message.results.length === 1 ? '' : 's'}.
        </p>
        {#each message.results as result}
          <div class="card">
            <a class="cardtitle" href={`/papers/${result.id}`}>{result.title}</a>
            <p class="cardmeta">
              <span>{formatMonthYear(result.published_at)}</span>
              {#if result.venue}<span>{result.venue}</span>{/if}
              {#if result.relevance !== null}
                <span class="cardmatch">{Math.round(result.relevance * 100)}% match</span>
              {/if}
            </p>
            {#if result.matches.length > 0}
              <p class="cardmatches">Matches: {result.matches.join(', ')}</p>
            {/if}
            {#if result.keywords.length > 0}
              <p class="cardtags">
                {#each result.keywords as keyword}
                  <a href={`/?q=${encodeURIComponent(keyword)}`}>{keyword}</a>
                {/each}
              </p>
            {/if}
            {#if result.excerpt}
              <blockquote class="excerpt">{result.excerpt}</blockquote>
            {/if}
          </div>
        {/each}
      </div>
    {:else if formatted}
      <div class="bubble prose">{@html formatted}</div>
    {:else}
      <p class="bubble">
        {message.text}{#if streamingCaret}<span class="cursor" aria-hidden="true"></span>{/if}
      </p>
    {/if}
    {#if message.stopped}
      <p class="note">Stopped</p>
    {/if}
    {#if !message.results && message.cited && message.cited.length > 0}
      <p class="citations">
        {#each message.used ?? [] as paper}
          {#if message.cited.includes(paper.id)}
            <a href={`/papers/${paper.id}`} title={paper.title}>{paper.id}</a>
          {/if}
        {/each}
      </p>
    {/if}
    {#if message.mode === 'fast' && message.phase === 'done' && !message.error && message.cited && message.cited.length > 0}
      <p class="actions">
        <button class="ghost" onclick={onsummarize} disabled={busy}>Summarize with AI</button>
      </p>
    {/if}
    {#if (message.notfound && message.phase === 'done') || message.webHits}
      <div class="webfallback">
        {#if message.notfound?.webSearch && !message.webHits}
          <p class="actions">
            <button class="ghost" onclick={onwebsearch} disabled={message.webBusy}>
              {message.webBusy ? 'Searching the web' : 'Search the web'}
            </button>
          </p>
        {/if}
        {#if message.webHits}
          {#if message.webHits.length === 0}
            <p class="note">Nothing found on the web either.</p>
          {/if}
          {#each message.webHits as hit}
            <WebHitCard
              {hit}
              state={hit.arxiv_id ? message.imports?.[hit.arxiv_id] : undefined}
              {onimport}
            />
          {/each}
          {#if message.webFailed && message.webFailed.length > 0}
            <p class="note">Search unavailable for: {message.webFailed.join(', ')}</p>
          {/if}
        {/if}
      </div>
    {/if}
  </div>
</div>

<style>
  .msg {
    display: flex;
    gap: 0.5rem;
    animation: msg-in 0.18s ease-out;
  }
  .msg.user {
    justify-content: flex-end;
  }
  @keyframes msg-in {
    from {
      opacity: 0;
      transform: translateY(4px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
  .avatar {
    flex-shrink: 0;
    margin-top: 0.4rem;
    color: var(--ink, #17191c);
  }
  .content {
    display: flex;
    flex-direction: column;
    min-width: 0;
    flex: 1;
  }
  .bubble {
    margin: 0;
    padding: 0.6rem 0.9rem;
    border-radius: 14px;
    font-size: 0.92rem;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    color: var(--ink, #17191c);
  }
  .msg.user .bubble {
    background: var(--accent, #c2410c);
    color: var(--accent-contrast, #fff);
    margin-left: 2rem;
    align-self: flex-end;
    border-bottom-right-radius: 4px;
  }
  .msg.assistant .bubble {
    background: var(--surface-2, #f4f0e8);
    border: 1px solid var(--line, #e6e1d5);
    margin-right: 2rem;
    border-bottom-left-radius: 4px;
  }
  .msg.error .bubble {
    background: #fdecec;
    border-color: #f5c8c8;
    color: #8b1d1d;
  }
  /* The design system has no error tokens, so the dark values live here. */
  :global([data-theme='dark']) .msg.error .bubble {
    background: #3a2020;
    border-color: #5c3434;
    color: #f2b8b8;
  }
  .prose {
    white-space: normal;
  }
  .prose :global(p) {
    margin: 0 0 0.5rem;
  }
  .prose :global(p:last-child) {
    margin-bottom: 0;
  }
  .prose :global(a) {
    color: var(--accent-ink, #78350f);
    font-weight: 500;
  }
  .prose :global(code) {
    background: var(--surface, #fff);
    border: 1px solid var(--line, #e6e1d5);
    border-radius: 4px;
    padding: 0 0.25rem;
    font-size: 0.85em;
  }
  .pending {
    color: var(--muted, #5d6570);
  }
  .cards {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    margin-right: 2rem;
  }
  .lead {
    margin: 0;
    font-size: 0.85rem;
    color: var(--muted, #5d6570);
  }
  .card {
    border: 1px solid var(--line, #e6e1d5);
    border-radius: 10px;
    padding: 0.6rem 0.75rem;
    background: var(--surface, #fff);
  }
  .cardtitle {
    display: block;
    font-size: 0.88rem;
    font-weight: 600;
    color: var(--ink, #17191c);
    text-decoration: none;
  }
  .cardtitle:hover {
    color: var(--accent, #c2410c);
    text-decoration: underline;
  }
  .cardmeta {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin: 0.15rem 0 0;
    font-size: 0.72rem;
    color: var(--muted, #5d6570);
  }
  .cardmatch {
    margin-left: auto;
    font-weight: 500;
    color: var(--accent-ink, #78350f);
  }
  .cardmatches {
    margin: 0.25rem 0 0;
    font-size: 0.75rem;
    color: var(--muted, #5d6570);
  }
  .cardtags {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
    margin: 0.35rem 0 0;
  }
  .cardtags a {
    font-size: 0.72rem;
    font-weight: 500;
    background: var(--accent-soft, #fef3c7);
    border-radius: 999px;
    padding: 0.05rem 0.55rem;
    text-decoration: none;
    color: var(--accent-ink, #78350f);
    transition: background-color 0.15s ease;
  }
  .cardtags a:hover {
    background: var(--chip-hover, #fde68a);
  }
  .excerpt {
    margin: 0.4rem 0 0;
    padding: 0.1rem 0 0.1rem 0.6rem;
    border-left: 3px solid var(--line-strong, #d1d6dc);
    font-size: 0.78rem;
    color: var(--muted, #5d6570);
  }
  .note {
    margin: 0.25rem 0 0;
    font-size: 0.78rem;
    color: var(--muted, #5d6570);
  }
  .citations {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin: 0;
    padding: 0.4rem 0 0;
  }
  .citations a {
    font-size: 0.75rem;
    font-weight: 500;
    background: var(--accent-soft, #fef3c7);
    border-radius: 999px;
    padding: 0.05rem 0.55rem;
    text-decoration: none;
    color: var(--accent-ink, #78350f);
    transition: background-color 0.15s ease;
  }
  .citations a:hover {
    background: var(--chip-hover, #fde68a);
  }
  .actions {
    margin: 0;
    padding: 0.4rem 0 0;
  }
  .ghost {
    border: 1px solid var(--line, #e6e1d5);
    border-radius: 999px;
    background: var(--surface, #fff);
    color: var(--ink, #17191c);
    font: inherit;
    font-size: 0.78rem;
    font-weight: 500;
    padding: 0.25rem 0.75rem;
    cursor: pointer;
    transition: background-color 0.15s ease;
  }
  .ghost:hover:not(:disabled) {
    background: var(--surface-2, #f4f0e8);
  }
  .ghost:disabled {
    opacity: 0.55;
    cursor: default;
  }
  .ghost:focus-visible {
    outline: 2px solid var(--accent, #c2410c);
    outline-offset: 2px;
  }
  .webfallback {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    margin: 0.4rem 2rem 0 0;
  }
  .cursor {
    display: inline-block;
    width: 3px;
    height: 1em;
    margin-left: 2px;
    vertical-align: text-bottom;
    background: currentColor;
    animation: blink 1s step-start infinite;
  }
  @keyframes blink {
    50% {
      opacity: 0;
    }
  }
  .dots span {
    display: inline-block;
    animation: dot-fade 1.2s infinite;
  }
  .dots span:nth-child(2) {
    animation-delay: 0.2s;
  }
  .dots span:nth-child(3) {
    animation-delay: 0.4s;
  }
  @keyframes dot-fade {
    0%,
    60%,
    100% {
      opacity: 0.25;
    }
    20% {
      opacity: 1;
    }
  }
  /* Stilled under reduced motion, like the drawer slide. */
  @media (prefers-reduced-motion: reduce) {
    .msg,
    .cursor,
    .dots span {
      animation: none;
    }
  }
  @media (max-width: 480px) {
    .msg.user .bubble {
      margin-left: 1.25rem;
    }
    .msg.assistant .bubble {
      margin-right: 1.25rem;
    }
    .cards,
    .webfallback {
      margin-right: 1.25rem;
    }
  }
</style>
