<script lang="ts">
  // Light/dark switch. The pre-paint inline script in Base.astro has already set
  // <html data-theme>; this island flips it through the shared applier (which also keeps
  // the browser-chrome color in step) and follows the settings drawer's announcements, so
  // the icon stays honest when the change comes from there - including a System choice
  // this binary control cannot express, which it shows as whatever the OS resolved to.

  import { Moon, Sun } from 'lucide-svelte';

  import { applyTheme, withViewTransition } from '../lib/prefs';

  let theme = $state<'light' | 'dark'>('light');

  $effect(() => {
    theme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  });

  $effect(() => {
    const follow = (event: Event) => {
      const resolved = (event as CustomEvent).detail?.resolved;
      if (resolved === 'dark' || resolved === 'light') theme = resolved;
    };
    document.addEventListener('rs:themechange', follow);
    return () => document.removeEventListener('rs:themechange', follow);
  });

  function flip() {
    // Computed before the wrap so the callback holds only mutations; the transition
    // cross-fades the whole page to the new theme instead of cutting.
    const next = theme === 'dark' ? 'light' : 'dark';
    withViewTransition(() => {
      theme = applyTheme(next);
    });
  }
</script>

<button
  class="toggle"
  onclick={flip}
  aria-label={theme === 'dark' ? 'Switch to the light theme' : 'Switch to the dark theme'}
  title={theme === 'dark' ? 'Switch to the light theme' : 'Switch to the dark theme'}
>
  {#key theme}
    <span class="icon">
      {#if theme === 'dark'}
        <Sun size={16} aria-hidden="true" />
      {:else}
        <Moon size={16} aria-hidden="true" />
      {/if}
    </span>
  {/key}
</button>

<style>
  /* Keeps its border: one of the three header controls that is a control rather than a link. */
  .toggle {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2.15rem;
    height: 2.15rem;
    flex-shrink: 0;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: 999px;
    background: var(--surface, #fff);
    color: var(--muted, #5d6570);
    cursor: pointer;
    transition:
      background-color var(--dur-fast, 0.15s) var(--ease-out, ease),
      border-color var(--dur-fast, 0.15s) var(--ease-out, ease),
      color var(--dur-fast, 0.15s) var(--ease-out, ease);
  }
  .toggle:hover {
    background: var(--surface-2, #f4f0e8);
    border-color: var(--line-strong, #d1d6dc);
    color: var(--ink, #17191c);
  }
  .toggle:focus-visible {
    outline: 2px solid var(--accent, #c2410c);
    outline-offset: 2px;
  }
  /* The keyed swap mounts a fresh icon per theme; it arrives with a small settle-spin,
     whichever island changed the theme (the drawer's change lands here via
     rs:themechange). */
  .icon {
    display: inline-flex;
    animation: icon-swap var(--dur-fast, 0.15s) var(--ease-out, ease);
  }
  @keyframes icon-swap {
    from {
      opacity: 0;
      transform: rotate(-40deg) scale(0.7);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .toggle {
      transition: none;
    }
    .icon {
      animation: none;
    }
  }
</style>
