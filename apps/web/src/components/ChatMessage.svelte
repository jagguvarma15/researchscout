<script lang="ts">
  // One chat exchange entry in the document layout: the user's question as a compact
  // right-aligned pill, Scout's side as full-width blocks - status line, fast-mode result
  // cards, formatted prose, citations, actions, and the web-search fallback - all sharing
  // one column edge. All state lives in the parent; this component only renders and
  // forwards intents.
  import type { Message, WebHit } from '../lib/chat-types';
  import { formatMonthYear, renderAnswerHtml } from '../lib/chat-format';
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
      if (current.webBusy) return 'Searching the web';
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
  // Stopped answers format too - a half answer still reads better with its lists intact.
  const formatted = $derived(
    message.role === 'assistant' &&
      message.mode === 'llm' &&
      (message.phase === 'done' || message.stopped) &&
      !message.error &&
      message.text
      ? renderAnswerHtml(message.text, new Set((message.used ?? []).map((paper) => paper.id)))
      : null,
  );
</script>

<div class="msg {message.role}" class:error={message.error}>
  {#if status}
    <p class="status" role="status">
      <!-- Keyed so each phase label arrives with a small fade instead of snapping; the
           dots stay outside the key and never restart. -->
      {#key status}<span class="phase">{status}</span>{/key}<span class="dots" aria-hidden="true"
        ><span>.</span><span>.</span><span>.</span></span
      >
    </p>
  {:else if message.results}
    <div class="cards">
      <p class="lead">
        Found {message.results.length} matching paper{message.results.length === 1 ? '' : 's'}.
      </p>
      {#each message.results as result, index}
        <!-- The whole block mounts at once; the capped per-card delay turns that into a
             short cascade. -->
        <div class="result-card" style="--i: {Math.min(index, 5)}">
          <div class="cardhead">
            <a class="cardtitle" href={`/papers/${result.id}`}>{result.title}</a>
            {#if result.relevance !== null}
              <span class="cardmatch">{Math.round(result.relevance * 100)}%</span>
            {/if}
          </div>
          <p class="cardmeta">
            <span>{formatMonthYear(result.published_at)}</span>
            {#if result.venue}<span>{result.venue}</span>{/if}
          </p>
          {#if result.matches.length > 0}
            <p class="cardmatches">Matches: {result.matches.join(', ')}</p>
          {/if}
          {#if result.keywords.length > 0}
            <p class="cardtags">
              <!-- Four is a scan; seven long phrases were noise even styled. -->
              {#each result.keywords.slice(0, 4) as keyword}
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
  {:else if message.error && message.text}
    <p class="errorbox">{message.text}</p>
  {:else if formatted}
    <div class="prose">{@html formatted}</div>
  {:else if message.role === 'user'}
    <p class="bubble">{message.text}</p>
  {:else if message.text || streamingCaret}
    <!-- A /web message carries no text of its own; skip the empty block. -->
    <p class="plain">
      {message.text}{#if streamingCaret}<span class="cursor" aria-hidden="true"></span>{/if}
    </p>
  {/if}
  {#if message.needsSignIn}
    <p class="actions">
      <a class="ghost" href="/login">Sign in to continue</a>
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

<style>
  /* One column for everything: the user pill aligns itself right inside it, and every
     assistant block shares the same left and right edges. The old two-margin layout put
     the user bubble 2rem right of assistant content while chips and buttons jutted past
     the bubble they belonged to. */
  .msg {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    min-width: 0;
    animation: msg-in var(--dur-fast, 0.15s) var(--ease-out, cubic-bezier(0.2, 0, 0, 1));
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
  /* An inline box cannot be transformed; the phase label needs one for its entrance. */
  .phase {
    display: inline-block;
    animation: phase-in var(--dur-fast, 0.15s) var(--ease-out, cubic-bezier(0.2, 0, 0, 1));
  }
  @keyframes phase-in {
    from {
      opacity: 0;
      transform: translateY(2px);
    }
  }
  .bubble {
    align-self: flex-end;
    max-width: 85%;
    margin: 0;
    padding: 0.5rem 0.85rem;
    border-radius: var(--radius-md, 14px);
    border-bottom-right-radius: 4px;
    background: var(--accent, #c2410c);
    color: var(--accent-contrast, #fff);
    font-size: var(--text-sm, 0.875rem);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .plain {
    margin: 0;
    font-size: 0.95rem;
    line-height: 1.6;
    color: var(--ink, #17191c);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .status {
    margin: 0;
    font-size: var(--text-xs, 0.75rem);
    color: var(--muted, #5d6570);
  }
  .errorbox {
    margin: 0;
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--danger, #b91c1c);
    border-radius: var(--radius-sm, 10px);
    background: var(--danger-soft, #fee2e2);
    color: var(--danger-ink, #7f1d1d);
    font-size: var(--text-sm, 0.875rem);
    overflow-wrap: anywhere;
  }
  /* The prose rhythm follows .digest-body (global.css): same size, same line height, so
     an answer reads like the site's other long-form text. */
  .prose {
    font-size: 0.95rem;
    line-height: 1.65;
    color: var(--ink, #17191c);
    overflow-wrap: anywhere;
    /* The streamed paragraph and this formatted block are different nodes; the fade turns
       the swap at completion into a settle instead of a flicker. Mounts once per answer -
       later renders reuse the node. */
    animation: msg-in var(--dur-fast, 0.15s) var(--ease-out, cubic-bezier(0.2, 0, 0, 1));
  }
  .prose :global(p) {
    margin: 0 0 0.75em;
  }
  .prose :global(ul),
  .prose :global(ol) {
    margin: 0 0 0.75em;
    padding-left: 1.25em;
  }
  .prose :global(li) {
    margin: 0.2em 0;
  }
  .prose :global(h4) {
    margin: 0.9em 0 0.35em;
    font-size: var(--text-md, 1rem);
    font-weight: 650;
  }
  .prose :global(h4:first-child) {
    margin-top: 0;
  }
  .prose :global(pre) {
    margin: 0 0 0.75em;
    padding: 0.6rem 0.75rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-sm, 10px);
    background: var(--surface-2, #f4f0e8);
    overflow-x: auto;
    font-size: 0.8rem;
    line-height: 1.5;
  }
  .prose :global(pre code) {
    border: none;
    background: none;
    padding: 0;
    font-size: inherit;
  }
  .prose :global(> :last-child) {
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
  .cards {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .lead {
    margin: 0;
    font-size: 0.9rem;
    color: var(--muted, #5d6570);
  }
  .result-card {
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-sm, 10px);
    padding: 0.65rem 0.8rem;
    background: var(--surface, #fff);
    /* The both fill hides a card until its capped delay elapses, which is what turns the
       block mount into a cascade. Delayed fill-both needs the explicit reduced-motion
       opt-outs below - the universal guard shortens durations but leaves delays alone. */
    animation: msg-in var(--dur-fast, 0.15s) var(--ease-out, cubic-bezier(0.2, 0, 0, 1)) both;
    animation-delay: calc(var(--i, 0) * 40ms);
  }
  /* Title and match share one row; the badge cannot wander under the date the way the
     old auto-margin pill did on a wrapped meta row. */
  .cardhead {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .cardtitle {
    font-size: var(--text-sm, 0.875rem);
    font-weight: 600;
    color: var(--ink, #17191c);
    text-decoration: none;
  }
  .cardtitle:hover {
    color: var(--accent, #c2410c);
    text-decoration: underline;
  }
  .cardmatch {
    flex-shrink: 0;
    font-size: var(--text-xs, 0.75rem);
    font-weight: 600;
    color: var(--accent-ink, #78350f);
    background: var(--accent-soft, #fef3c7);
    border-radius: var(--radius-full, 999px);
    padding: 0.1rem 0.5rem;
  }
  .cardmeta {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin: 0.15rem 0 0;
    font-size: var(--text-xs, 0.75rem);
    color: var(--muted, #5d6570);
  }
  .cardmatches {
    margin: 0.25rem 0 0;
    font-size: var(--text-xs, 0.75rem);
    color: var(--muted, #5d6570);
  }
  .cardtags {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
    margin: 0.35rem 0 0;
  }
  .cardtags a,
  .citations a {
    font-size: var(--text-xs, 0.75rem);
    font-weight: 500;
    background: var(--accent-soft, #fef3c7);
    border-radius: var(--radius-full, 999px);
    padding: 0.1rem 0.6rem;
    text-decoration: none;
    color: var(--accent-ink, #78350f);
    transition: background-color var(--dur-fast, 0.15s) var(--ease-out, cubic-bezier(0.2, 0, 0, 1));
  }
  .cardtags a:hover,
  .citations a:hover {
    background: var(--chip-hover, #fde68a);
  }
  .excerpt {
    margin: 0.4rem 0 0;
    padding: 0.1rem 0 0.1rem 0.6rem;
    border-left: 3px solid var(--line-strong, #d1d6dc);
    font-size: 0.8rem;
    color: var(--muted, #5d6570);
  }
  .note {
    margin: 0;
    font-size: var(--text-xs, 0.75rem);
    color: var(--muted, #5d6570);
  }
  .citations {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin: 0;
  }
  .actions {
    margin: 0;
  }
  /* Shared by the action buttons and the sign-in link, which is an anchor because it
     navigates - hence the display and text-decoration resets. */
  .ghost {
    display: inline-block;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-full, 999px);
    background: var(--surface, #fff);
    color: var(--ink, #17191c);
    font: inherit;
    font-size: 0.8rem;
    font-weight: 500;
    padding: 0.35rem 0.85rem;
    text-decoration: none;
    cursor: pointer;
    transition: background-color var(--dur-fast, 0.15s) var(--ease-out, cubic-bezier(0.2, 0, 0, 1));
  }
  a.ghost:hover,
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
    .dots span,
    .phase,
    .result-card,
    .prose {
      animation: none;
    }
  }
  /* The site switch needs the card called out by name: its delayed both fill would
     otherwise blank each card for the length of its delay. */
  :global(html[data-motion='reduced']) .result-card {
    animation: none;
  }
  /* The app-wide phone tier, not the stray 480px the old layout used. */
  @media (max-width: 40rem) {
    .bubble {
      max-width: 92%;
    }
  }
</style>
