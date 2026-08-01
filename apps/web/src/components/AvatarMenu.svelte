<script lang="ts">
  // Header account menu: an initials avatar that opens a small dropdown with the
  // profile link. Closes on click-outside and Escape.

  let { username }: { username: string } = $props();

  let open = $state(false);
  let root: HTMLElement | undefined = $state();

  const initials =
    username
      .split(/[\s._@-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join('')
      .toUpperCase() || '?';

  function onDocumentClick(event: MouseEvent) {
    if (open && root && !root.contains(event.target as Node)) open = false;
  }

  function onDocumentKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') open = false;
  }
</script>

<svelte:document onclick={onDocumentClick} onkeydown={onDocumentKeydown} />

<div class="avatar-menu" bind:this={root}>
  <button
    class="avatar"
    onclick={() => (open = !open)}
    aria-expanded={open}
    aria-haspopup="menu"
    aria-label={`Account menu for ${username}`}
    title={username}
  >
    {initials}
  </button>
  {#if open}
    <div class="menu" role="menu" aria-label="Account">
      <span class="menu-user" role="presentation">{username}</span>
      <a role="menuitem" href="/profile">Profile settings</a>
      <a role="menuitem" href="/logout">Sign out</a>
    </div>
  {/if}
</div>

<style>
  .avatar-menu {
    position: relative;
    display: inline-flex;
  }
  .avatar {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2.15rem;
    height: 2.15rem;
    border: none;
    border-radius: 999px;
    background: var(--accent-soft, #fef3c7);
    color: var(--accent-ink, #78350f);
    font: inherit;
    font-size: 0.8rem;
    font-weight: 650;
    letter-spacing: 0.02em;
    cursor: pointer;
    transition: background-color 0.15s ease;
  }
  .avatar:hover {
    background: var(--chip-hover, #fde68a);
  }
  .avatar:focus-visible {
    outline: 2px solid var(--accent, #c2410c);
    outline-offset: 2px;
  }
  .menu {
    position: absolute;
    top: calc(100% + 0.5rem);
    right: 0;
    z-index: 20;
    min-width: 11rem;
    display: flex;
    flex-direction: column;
    padding: 0.4rem;
    border: 1px solid var(--line, #e4e7eb);
    border-radius: 12px;
    background: var(--surface, #fff);
    box-shadow: var(--shadow-md, 0 12px 32px rgb(23 25 28 / 0.1));
  }
  .menu-user {
    padding: 0.35rem 0.65rem 0.45rem;
    border-bottom: 1px solid var(--line, #e4e7eb);
    margin-bottom: 0.3rem;
    color: var(--muted, #5d6570);
    font-size: 0.78rem;
  }
  .menu a {
    padding: 0.45rem 0.65rem;
    border-radius: 8px;
    color: var(--ink, #17191c);
    font-size: 0.88rem;
    text-decoration: none;
  }
  .menu a:hover {
    background: var(--surface-2, #f5f7fa);
  }
  .menu a:focus-visible {
    outline: 2px solid var(--accent, #c2410c);
    outline-offset: -2px;
  }
  @media (prefers-reduced-motion: reduce) {
    .avatar {
      transition: none;
    }
  }
</style>
