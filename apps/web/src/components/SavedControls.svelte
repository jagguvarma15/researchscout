<script lang="ts">
  // The per-row library controls on /saved: reading status as an immediate select, tags
  // and the note behind a small editor. Each change PATCHes through the proxy; the row
  // keeps its own view current and the page's server-rendered filters catch up on the
  // next load - a whole-page refresh over one select would cost more than it tells.

  let {
    paperId,
    status,
    tags,
    note,
  }: {
    paperId: string;
    status: string;
    tags: string[];
    note: string | null;
  } = $props();

  let current = $state(status);
  let editing = $state(false);
  let tagsText = $state(tags.join(', '));
  let noteText = $state(note ?? '');
  let phase = $state<'idle' | 'busy' | 'saved' | 'error'>('idle');

  async function patch(body: Record<string, unknown>): Promise<void> {
    phase = 'busy';
    try {
      const response = await fetch(`/api/papers/${encodeURIComponent(paperId)}/save`, {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(String(response.status));
      phase = 'saved';
      setTimeout(() => {
        if (phase === 'saved') phase = 'idle';
      }, 1500);
    } catch {
      phase = 'error';
    }
  }

  function onStatusChange(): void {
    void patch({ status: current });
  }

  function saveEditor(): void {
    const cleaned = tagsText
      .split(',')
      .map((tag) => tag.trim())
      .filter(Boolean)
      .slice(0, 20);
    tagsText = cleaned.join(', ');
    void patch({ tags: cleaned, note: noteText.trim() || null });
    editing = false;
  }
</script>

<div class="controls">
  <select
    class="status"
    bind:value={current}
    onchange={onStatusChange}
    aria-label="Reading status"
  >
    <option value="to-read">To read</option>
    <option value="reading">Reading</option>
    <option value="done">Done</option>
  </select>
  <button class="edit" type="button" onclick={() => (editing = !editing)}>
    {editing ? 'Close' : 'Tags and note'}
  </button>
  {#if phase === 'busy'}<span class="state">Saving</span>{/if}
  {#if phase === 'saved'}<span class="state">Saved</span>{/if}
  {#if phase === 'error'}<span class="state err">Could not save - try again</span>{/if}
</div>

{#if editing}
  <div class="editor">
    <label>
      <span class="label">Tags, comma separated</span>
      <input type="text" bind:value={tagsText} maxlength="400" placeholder="reading-group, rl" />
    </label>
    <label>
      <span class="label">Note</span>
      <textarea bind:value={noteText} rows="3" maxlength="4000" placeholder="Why this paper matters to you"></textarea>
    </label>
    <button class="save btn btn-primary" type="button" onclick={saveEditor}>Save</button>
  </div>
{/if}

<style>
  .controls {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .status {
    font: inherit;
    font-size: var(--text-xs);
    color: var(--ink);
    background: var(--surface-2);
    border: 1px solid var(--line);
    border-radius: var(--radius-full);
    padding: 0.12rem 0.5rem;
  }
  .edit {
    border: 0;
    background: none;
    padding: 0;
    font: inherit;
    font-size: var(--text-xs);
    color: var(--muted);
    cursor: pointer;
    text-decoration: underline;
  }
  .edit:hover {
    color: var(--ink);
  }
  .state {
    font-size: var(--text-xs);
    color: var(--muted);
  }
  .state.err {
    color: var(--danger, #b3261e);
  }
  .editor {
    display: grid;
    gap: 0.6rem;
    margin-top: 0.6rem;
    padding: 0.75rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-md, 10px);
    background: var(--surface-2);
    max-width: 34rem;
  }
  .label {
    display: block;
    margin-bottom: 0.25rem;
    color: var(--muted);
    font-size: 0.68rem;
    font-weight: var(--weight-semibold);
    letter-spacing: var(--track-caps);
    text-transform: uppercase;
  }
  input,
  textarea {
    width: 100%;
    font: inherit;
    font-size: var(--text-sm);
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 0.4rem 0.6rem;
  }
  textarea {
    resize: vertical;
  }
  .save {
    justify-self: start;
  }
</style>
