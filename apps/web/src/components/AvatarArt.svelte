<script lang="ts">
  // The crew: ten owls in ScoutMascot's style, one per role, told apart by a single prop
  // and a backdrop of their own. Original drawings - the roles are the classic pirate-crew
  // ones, the props deliberately generic (a straw hat is a hat).
  //
  // Same discipline as the mascot: every shape carries presentation attributes AND a class.
  // Attributes draw a correct line figure in currentColor if the stylesheet never arrives;
  // the CSS then colours it in, because a rule beats a presentation attribute. Decorative
  // wherever it appears; the accessible name lives on the control that contains it.

  import type { AvatarSlug } from '../lib/avatars';

  let { slug, size = 40 }: { slug: AvatarSlug; size?: number } = $props();

  // These wear something on the head, so the ear tufts stay under it.
  const covered = slug === 'captain' || slug === 'swordsman' || slug === 'cook' || slug === 'archaeologist';
</script>

<svg
  class="owl-avatar {slug}"
  viewBox="0 0 64 64"
  width={size}
  height={size}
  aria-hidden="true"
  fill="none"
>
  <circle cx="32" cy="32" r="30" class="bg" fill="none" />
  {#if !covered}
    <path d="M17 23 L22 13 L27 21 Z" class="body" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
    <path d="M47 23 L42 13 L37 21 Z" class="body" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
  {/if}
  <circle cx="32" cy="38" r="19" class="body" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
  <circle cx="24.5" cy="33" r="6" class="ring" fill="none" stroke="currentColor" stroke-width="2.5" />
  <circle cx="39.5" cy="33" r="6" class="ring" fill="none" stroke="currentColor" stroke-width="2.5" />
  <circle cx="24.5" cy="33" r="2.4" class="eye" fill="currentColor" />
  <circle cx="39.5" cy="33" r="2.4" class="eye" fill="currentColor" />
  <path d="M32 45.5 L28.5 40.5 L35.5 40.5 Z" class="beak" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" />
  {#if slug === 'captain'}
    <path d="M22 19 Q22 7 32 7 Q42 7 42 19 Z" class="straw" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
    <path d="M22 15.5 L42 15.5 L42 19 L22 19 Z" class="hatband" fill="currentColor" />
    <ellipse cx="32" cy="20" rx="17" ry="4.5" class="straw" fill="none" stroke="currentColor" stroke-width="2.5" />
  {:else if slug === 'swordsman'}
    <path d="M20 22 Q23 11 32 11 Q41 11 44 22 Z" class="bandana" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
    <path d="M44 20 L53 15 L50 24 Z" class="bandana" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
    <line x1="45.5" y1="52.5" x2="57" y2="41" class="blade" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
    <line x1="46" y1="45" x2="52" y2="51" class="guard" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
  {:else if slug === 'navigator'}
    <circle cx="47" cy="48" r="8" class="dial" fill="none" stroke="currentColor" stroke-width="2.5" />
    <path d="M47 42 L49.3 48 L44.7 48 Z" class="needle-n" fill="currentColor" />
    <path d="M47 54 L44.7 48 L49.3 48 Z" class="needle-s" fill="currentColor" />
    <circle cx="47" cy="48" r="1.3" class="eye" fill="currentColor" />
  {:else if slug === 'cook'}
    <path d="M23 18 Q21 9 28 9 Q32 4 37 8 Q44 7 41 18 Z" class="toque" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
    <path d="M23 18 L41 18 L41 22 L23 22 Z" class="toque" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
  {:else if slug === 'sniper'}
    <line x1="42" y1="37" x2="50" y2="43" class="scope" stroke="currentColor" stroke-width="5" stroke-linecap="round" />
    <line x1="50" y1="43" x2="57.5" y2="48.5" class="scope-thin" stroke="currentColor" stroke-width="3" stroke-linecap="round" />
  {:else if slug === 'doctor'}
    <path d="M25.5 44 Q25.5 53 32.5 53 Q39.5 53 39.5 46.5 L44.5 50" class="steth" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
    <circle cx="46.5" cy="51.5" r="3.2" class="dial" fill="none" stroke="currentColor" stroke-width="2.5" />
  {:else if slug === 'shipwright'}
    <line x1="43" y1="57" x2="52" y2="48" class="tool" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" />
    <path d="M49.5 42.5 A 5.7 5.7 0 1 0 57.5 50.5" class="tool" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" />
  {:else if slug === 'musician'}
    <line x1="50" y1="35" x2="50" y2="50" class="note" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
    <path d="M50 35 Q56.5 37.5 54.5 44" class="note" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
    <ellipse cx="47" cy="50.5" rx="3.4" ry="2.6" class="eye" fill="currentColor" />
  {:else if slug === 'archaeologist'}
    <path d="M16 21 Q21 7.5 32 7.5 Q43 7.5 48 21 L43.5 18.5 Q38 14 32 14 Q26 14 20.5 18.5 Z" class="tricorne" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
  {:else if slug === 'helmsman'}
    <circle cx="32" cy="51.5" r="8" class="wheel" stroke="currentColor" stroke-width="2.5" fill="none" />
    <line x1="40" y1="51.5" x2="43" y2="51.5" class="wheel" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
    <line x1="37.66" y1="57.16" x2="39.78" y2="59.28" class="wheel" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
    <line x1="32" y1="59.5" x2="32" y2="62.5" class="wheel" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
    <line x1="26.34" y1="57.16" x2="24.22" y2="59.28" class="wheel" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
    <line x1="24" y1="51.5" x2="21" y2="51.5" class="wheel" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
    <line x1="26.34" y1="45.84" x2="24.22" y2="43.72" class="wheel" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
    <line x1="32" y1="43.5" x2="32" y2="40.5" class="wheel" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
    <line x1="37.66" y1="45.84" x2="39.78" y2="43.72" class="wheel" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
    <circle cx="32" cy="51.5" r="2.2" class="wheel-hub" fill="currentColor" />
  {/if}
</svg>

<style>
  /* Each owl brings a backdrop of its own; the default is the accent soft so an unknown
     future slug still looks dressed. Dark variants are deep takes on the same hue. */
  .owl-avatar {
    --owl-bg: var(--accent-soft, #fef3c7);
    display: block;
  }
  .captain {
    --owl-bg: #fde68a;
  }
  .swordsman {
    --owl-bg: #d1fae5;
  }
  .navigator {
    --owl-bg: #dbeafe;
  }
  .cook {
    --owl-bg: #ffe4e6;
  }
  .sniper {
    --owl-bg: #fef9c3;
  }
  .doctor {
    --owl-bg: #dcfce7;
  }
  .shipwright {
    --owl-bg: #fed7aa;
  }
  .musician {
    --owl-bg: #f3e8ff;
  }
  .archaeologist {
    --owl-bg: #e7e5e4;
  }
  .helmsman {
    --owl-bg: #cffafe;
  }
  :global(html[data-theme='dark']) .captain {
    --owl-bg: #3a2c0f;
  }
  :global(html[data-theme='dark']) .swordsman {
    --owl-bg: #113029;
  }
  :global(html[data-theme='dark']) .navigator {
    --owl-bg: #152944;
  }
  :global(html[data-theme='dark']) .cook {
    --owl-bg: #3d2026;
  }
  :global(html[data-theme='dark']) .sniper {
    --owl-bg: #35300f;
  }
  :global(html[data-theme='dark']) .doctor {
    --owl-bg: #122f1c;
  }
  :global(html[data-theme='dark']) .shipwright {
    --owl-bg: #3b2410;
  }
  :global(html[data-theme='dark']) .musician {
    --owl-bg: #2f1b45;
  }
  :global(html[data-theme='dark']) .archaeologist {
    --owl-bg: #2b2925;
  }
  :global(html[data-theme='dark']) .helmsman {
    --owl-bg: #103138;
  }

  .bg {
    fill: var(--owl-bg);
  }
  .body {
    fill: var(--surface, #fff);
    stroke: currentColor;
    stroke-width: 2.5;
    stroke-linejoin: round;
  }
  .ring {
    fill: var(--surface, #fff);
    stroke: currentColor;
    stroke-width: 2.5;
  }
  .eye {
    fill: currentColor;
  }
  .beak {
    fill: var(--accent, #c2410c);
    stroke: var(--accent, #c2410c);
    stroke-width: 1.5;
    stroke-linejoin: round;
  }
  /* Straw stays straw and wood stays wood in both themes; the currentColor outlines are
     what keep them legible on the dark backdrops. */
  .straw {
    fill: #fcd34d;
    stroke: currentColor;
    stroke-width: 2.5;
    stroke-linejoin: round;
  }
  .hatband {
    fill: #b91c1c;
    stroke: none;
  }
  .bandana {
    fill: var(--accent, #c2410c);
    stroke: currentColor;
    stroke-width: 2.5;
    stroke-linejoin: round;
  }
  .blade {
    stroke: var(--muted, #5d6570);
    stroke-width: 2.5;
    stroke-linecap: round;
  }
  .guard {
    stroke: var(--accent-ink, #78350f);
    stroke-width: 2.5;
    stroke-linecap: round;
  }
  .dial {
    fill: var(--surface, #fff);
    stroke: currentColor;
    stroke-width: 2.5;
  }
  .needle-n {
    fill: var(--accent, #c2410c);
  }
  .needle-s {
    fill: var(--muted, #5d6570);
  }
  .toque {
    fill: var(--surface, #fff);
    stroke: currentColor;
    stroke-width: 2.5;
    stroke-linejoin: round;
  }
  .scope,
  .scope-thin {
    stroke: var(--accent-ink, #78350f);
    stroke-linecap: round;
  }
  .scope {
    stroke-width: 5;
  }
  .scope-thin {
    stroke-width: 3;
  }
  .steth {
    fill: none;
    stroke: var(--accent, #c2410c);
    stroke-width: 2.5;
    stroke-linecap: round;
  }
  .tool {
    fill: none;
    stroke: var(--muted, #5d6570);
    stroke-linecap: round;
  }
  .note {
    fill: none;
    stroke: currentColor;
    stroke-width: 2.5;
    stroke-linecap: round;
  }
  .tricorne {
    fill: #57534e;
    stroke: currentColor;
    stroke-width: 2.5;
    stroke-linejoin: round;
  }
  .wheel {
    fill: none;
    stroke: #a16207;
    stroke-width: 2.5;
    stroke-linecap: round;
  }
  .wheel-hub {
    fill: #a16207;
  }
</style>
