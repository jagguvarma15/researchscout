<script lang="ts">
  // Header account menu: the account's chosen crew avatar (initials until one is picked, or
  // if a stored slug stops existing) opening a small dropdown with the profile link and the
  // settings drawer trigger. Closes on the shared outside-click judgement (composedPath,
  // not contains - lib/overlay.ts records the re-render bug that distinction fixed) and a
  // guarded Escape that hands focus back to the button; Tab cycles inside while open.

  import { clickedOutside, trapFocus } from '../lib/overlay';
  import { isAvatarSlug } from '../lib/avatars';
  import AvatarArt from './AvatarArt.svelte';

  let { username, avatar = null }: { username: string; avatar?: string | null } = $props();

  let open = $state(false);
  let root: HTMLElement | undefined = $state();
  let button: HTMLButtonElement | undefined = $state();

  const art = $derived(avatar !== null && isAvatarSlug(avatar) ? avatar : null);

  const initials =
    username
      .split(/[\s._@-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join('')
      .toUpperCase() || '?';

  function onDocumentClick(event: MouseEvent) {
    if (open && clickedOutside(event, root)) open = false;
  }

  function onDocumentKeydown(event: KeyboardEvent) {
    if (!open) return;
    if (event.key === 'Escape') {
      open = false;
      button?.focus();
    } else if (event.key === 'Tab' && root) {
      trapFocus(root, event);
    }
  }
</script>

<svelte:document onclick={onDocumentClick} onkeydown={onDocumentKeydown} />

<div class="avatar-menu" bind:this={root}>
  <button
    class="avatar"
    class:pictured={art !== null}
    bind:this={button}
    onclick={() => (open = !open)}
    aria-expanded={open}
    aria-haspopup="menu"
    aria-label={`Account menu for ${username}`}
    title={username}
  >
    {#if art !== null}
      <AvatarArt slug={art} size={32} />
    {:else}
      {initials}
    {/if}
  </button>
  {#if open}
    <div class="menu" role="menu" aria-label="Account">
      <span class="menu-user" role="presentation">{username}</span>
      <a role="menuitem" href="/profile">Profile</a>
      <!-- The drawer's document-level delegate reads the attribute off the bubbled click;
           closing here is this menu's own business. -->
      <button role="menuitem" type="button" data-open-settings onclick={() => (open = false)}>
        Settings
      </button>
      <a role="menuitem" href="/logout">Sign out</a>
    </div>
  {/if}
</div>

<style>
  .avatar-menu {
    position: relative;
    display: inline-flex;
  }
  /* One of the three header controls that keeps a border. With the navigation links now
     borderless, the ring is what separates "who you are" from "where to go". */
  .avatar {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2.15rem;
    height: 2.15rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: 999px;
    background: var(--accent-soft, #fef3c7);
    color: var(--accent-ink, #78350f);
    font: inherit;
    font-size: 0.8rem;
    font-weight: 650;
    letter-spacing: 0.02em;
    cursor: pointer;
    transition:
      background-color var(--dur-fast, 0.15s) var(--ease-out, cubic-bezier(0.2, 0, 0, 1)),
      border-color var(--dur-fast, 0.15s) var(--ease-out, cubic-bezier(0.2, 0, 0, 1));
  }
  .avatar:hover {
    background: var(--chip-hover, #fde68a);
    border-color: var(--line-strong, #d1d6dc);
  }
  .avatar:focus-visible {
    outline: 2px solid var(--accent, #c2410c);
    outline-offset: 2px;
  }
  /* With art in the ring the soft wash would tint the drawing's own backdrop; the surface
     lets the owl's circle read as the avatar. */
  .avatar.pictured {
    padding: 0;
    background: var(--surface, #fff);
  }
  .menu {
    position: absolute;
    top: calc(100% + 0.5rem);
    right: 0;
    z-index: var(--z-header, 20);
    min-width: 11rem;
    display: flex;
    flex-direction: column;
    padding: 0.4rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-md, 14px);
    background: var(--surface, #fff);
    box-shadow: var(--shadow-lg, 0 2px 4px rgb(23 25 28 / 0.06), 0 16px 48px rgb(23 25 28 / 0.16));
  }
  .menu-user {
    padding: 0.35rem 0.65rem 0.45rem;
    border-bottom: 1px solid var(--line, #e6e1d5);
    margin-bottom: 0.3rem;
    color: var(--muted, #5d6570);
    font-size: 0.78rem;
  }
  .menu a,
  .menu button {
    padding: 0.45rem 0.65rem;
    border: none;
    border-radius: 8px;
    background: none;
    color: var(--ink, #17191c);
    font: inherit;
    font-size: 0.88rem;
    text-align: left;
    text-decoration: none;
    cursor: pointer;
  }
  .menu a:hover,
  .menu button:hover {
    background: var(--surface-2, #f4f0e8);
  }
  .menu a:focus-visible,
  .menu button:focus-visible {
    outline: 2px solid var(--accent, #c2410c);
    outline-offset: -2px;
  }
  @media (prefers-reduced-motion: reduce) {
    .avatar {
      transition: none;
    }
  }
</style>
