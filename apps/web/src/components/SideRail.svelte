<script lang="ts">
  // The navigation rail: fixed to the right edge below the header on wide screens, and an
  // off-canvas panel below 64rem opened by the header's [data-open-rail] button.
  //
  // Which of the two it is, is decided entirely in CSS. That matters: the rail is
  // server-rendered as ordinary markup, so the links are in the HTML and work before this
  // island hydrates - and `visibility: hidden` (not `display: none`) is what takes the closed
  // panel out of the tab order and the accessibility tree without any script having to know
  // the viewport width. The script below owns only the open state, which exists solely for
  // the narrow case.

  import {
    Bookmark,
    Boxes,
    Gauge,
    Info,
    Newspaper,
    Sparkles,
    TrendingUp,
    X,
  } from 'lucide-svelte';
  import { onDestroy, type Component } from 'svelte';

  import { lockBodyScroll, trapFocus } from '../lib/overlay';

  let { current }: { current: string } = $props();

  // Grouped rather than listed: the rail carries three different kinds of destination now, and
  // eight undifferentiated rows read as a pile. The headings are list items with a
  // presentation role, so the whole rail stays one list to a screen reader.
  const SECTIONS: { title: string; items: { href: string; label: string; icon: Component }[] }[] = [
    {
      title: 'Discover',
      items: [
        { href: '/for-you', label: 'For you', icon: Sparkles },
        { href: '/topics', label: 'Trends', icon: TrendingUp },
        { href: '/digests', label: 'Digests', icon: Newspaper },
      ],
    },
    {
      title: 'AI landscape',
      items: [
        { href: '/models', label: 'Models', icon: Boxes },
        { href: '/benchmarks', label: 'Benchmarks', icon: Gauge },
      ],
    },
    {
      title: 'Library',
      items: [
        { href: '/saved', label: 'Reading list', icon: Bookmark },
        { href: '/about', label: 'About', icon: Info },
      ],
    },
  ];

  let open = $state(false);
  let panel: HTMLElement | undefined = $state();
  let previousFocus: Element | null = null;
  let unlockScroll: (() => void) | null = null;

  // A soft navigation can destroy this island while the menu is open; the scroll lock must
  // not outlive it on the next page's body.
  onDestroy(() => unlockScroll?.());

  // Prefix, not equality, so /digests/2026-08-03 still marks Digests as the current section.
  function isCurrent(href: string): boolean {
    return current === href || current.startsWith(`${href}/`);
  }

  function show() {
    previousFocus = document.activeElement;
    open = true;
    // Only ever reached from the trigger, which CSS hides above 64rem - so if we are here,
    // the rail is the modal panel and deserves a scroll lock.
    unlockScroll = lockBodyScroll();
  }

  function hide() {
    if (!open) return;
    open = false;
    unlockScroll?.();
    unlockScroll = null;
    if (previousFocus instanceof HTMLElement) previousFocus.focus();
  }

  $effect(() => {
    if (open) panel?.querySelector<HTMLElement>('a, button')?.focus();
  });

  $effect(() => {
    // Growing past the breakpoint turns the panel back into a plain rail; the scroll lock it
    // was holding would otherwise outlive the panel that justified it.
    const wide = window.matchMedia('(min-width: 64rem)');
    const onChange = () => {
      if (wide.matches) hide();
    };
    wide.addEventListener('change', onChange);
    return () => wide.removeEventListener('change', onChange);
  });

  function onDocumentClick(event: MouseEvent) {
    if ((event.target as Element).closest('[data-open-rail]')) {
      event.preventDefault();
      show();
    }
  }

  function onWindowKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape' && open) hide();
  }

  function onPanelKeydown(event: KeyboardEvent) {
    if (open && panel) trapFocus(panel, event);
  }
</script>

<svelte:document onclick={onDocumentClick} />
<svelte:window onkeydown={onWindowKeydown} />

<!-- Backdrop belongs to the narrow case only; CSS keeps it out of the way above 64rem. -->
<div class="backdrop" class:open role="presentation" onclick={hide}></div>

<nav
  class="rail"
  class:open
  aria-label="Sections"
  bind:this={panel}
  onkeydown={onPanelKeydown}
>
  <button class="close" onclick={hide} aria-label="Close the menu">
    <X size={18} aria-hidden="true" />
  </button>
  <ul>
    {#each SECTIONS as section}
      <li class="section-title" role="presentation">{section.title}</li>
      {#each section.items as item}
        <li>
          <a
            class="btn btn-nav"
            href={item.href}
            aria-current={isCurrent(item.href) ? 'page' : undefined}
          >
            <item.icon size={16} aria-hidden="true" />
            <span>{item.label}</span>
          </a>
        </li>
      {/each}
    {/each}
  </ul>
</nav>

<style>
  .rail {
    position: fixed;
    top: var(--nav-height, 3.75rem);
    right: 0;
    bottom: 0;
    /* Under the header, which floats the omnibox panel out of its own stacking context. */
    z-index: 12;
    width: var(--rail-width, 13rem);
    padding: var(--space-5, 1.25rem) 0.75rem;
    border-left: 1px solid var(--line, #e6e1d5);
    background: var(--bg, #faf7f1);
    overflow-y: auto;
    overscroll-behavior: contain;
  }
  .rail ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }
  /* Small, quiet and set in from the links it heads, so the eye reads the destinations first
     and the grouping only when it is looking for one. */
  .section-title {
    margin-top: 1.1rem;
    padding: 0 0.7rem 0.3rem;
    color: var(--muted, #5d6570);
    font-size: 0.7rem;
    font-weight: 650;
    letter-spacing: 0.07em;
    text-transform: uppercase;
  }
  .section-title:first-child {
    margin-top: 0;
  }
  /* Full width and left-aligned: these are destinations in a list, not buttons in a row. */
  .rail a {
    justify-content: flex-start;
    width: 100%;
    padding: 0.5rem 0.7rem;
    border-radius: var(--radius-sm, 10px);
  }
  .rail a:focus-visible {
    border-radius: var(--radius-sm, 10px);
  }
  .rail a :global(svg) {
    color: var(--muted, #5d6570);
  }
  .rail a[aria-current] :global(svg) {
    color: var(--accent-ink, #78350f);
  }
  /* The close button and backdrop exist for the narrow panel; both are revealed below. */
  .close,
  .backdrop {
    display: none;
  }

  @media (max-width: 64rem) {
    .rail {
      top: 0;
      /* Above the filter sidebar (35), which can already be open on the feed when the
         menu is reached; the last surface opened should be the one on top. */
      z-index: 38;
      width: min(17rem, 82vw);
      padding-top: 1rem;
      background: var(--surface, #fff);
      box-shadow: var(--shadow-lg, 0 16px 48px rgb(23 25 28 / 0.16));
      /* Hidden rather than merely off-screen: this is what keeps the closed panel out of
         the tab order without a script deciding the viewport width. */
      visibility: hidden;
      transform: translateX(100%);
      transition:
        transform var(--dur-slow, 0.25s) var(--ease-out, ease),
        visibility var(--dur-slow, 0.25s);
    }
    .rail.open {
      visibility: visible;
      transform: translateX(0);
    }
    .close {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 2rem;
      height: 2rem;
      margin: 0 0 0.5rem auto;
      border: none;
      border-radius: var(--radius-full, 999px);
      background: none;
      color: var(--muted, #5d6570);
      cursor: pointer;
    }
    .close:hover {
      background: var(--surface-2, #f4f0e8);
      color: var(--ink, #17191c);
    }
    .backdrop {
      display: block;
      position: fixed;
      inset: 0;
      z-index: 37;
      background: rgb(0 0 0 / 0.32);
      backdrop-filter: blur(6px);
      -webkit-backdrop-filter: blur(6px);
      opacity: 0;
      visibility: hidden;
      transition:
        opacity var(--dur-slow, 0.25s) var(--ease-out, ease),
        visibility var(--dur-slow, 0.25s);
    }
    .backdrop.open {
      opacity: 1;
      visibility: visible;
    }
  }

  /* Phone: the drawer is the primary navigation, so its targets get finger sizing, and the
     safe-area padding keeps the last link above a home indicator (live because the layout
     sets viewport-fit=cover). */
  @media (max-width: 40rem) {
    .rail {
      width: min(19rem, 88vw);
      padding-bottom: calc(1rem + env(safe-area-inset-bottom));
    }
    .rail .btn-nav {
      padding: 0.7rem 0.8rem;
    }
    .close {
      width: 2.5rem;
      height: 2.5rem;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .rail,
    .backdrop {
      transition: none;
    }
  }
</style>
