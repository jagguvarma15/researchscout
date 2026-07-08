<script lang="ts">
  // Light/dark switch. The pre-paint inline script in Base.astro has already set
  // <html data-theme>; this island only flips the attribute and persists the choice.

  import { Moon, Sun } from 'lucide-svelte';

  let theme = $state<'light' | 'dark'>('light');

  $effect(() => {
    theme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  });

  function flip() {
    theme = theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('rs-theme', theme);
  }
</script>

<button
  class="toggle"
  onclick={flip}
  aria-label={theme === 'dark' ? 'Switch to the light theme' : 'Switch to the dark theme'}
  title={theme === 'dark' ? 'Switch to the light theme' : 'Switch to the dark theme'}
>
  {#if theme === 'dark'}
    <Sun size={16} aria-hidden="true" />
  {:else}
    <Moon size={16} aria-hidden="true" />
  {/if}
</button>

<style>
  .toggle {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2.15rem;
    height: 2.15rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: 999px;
    background: var(--surface, #fff);
    color: var(--muted, #5d6570);
    cursor: pointer;
    transition:
      background-color 0.15s ease,
      border-color 0.15s ease,
      color 0.15s ease;
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
  @media (prefers-reduced-motion: reduce) {
    .toggle {
      transition: none;
    }
  }
</style>
