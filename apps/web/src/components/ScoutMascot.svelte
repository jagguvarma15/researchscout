<script lang="ts">
  // Scout's mascot: an owl librarian drawn as one inline SVG so it ships with no asset
  // pipeline and follows the theme through currentColor and the accent tokens. Purely
  // decorative everywhere it appears, hence aria-hidden.
  //
  // Every shape carries presentation attributes as well as a class. That is not redundancy:
  // with fills and strokes coming only from the scoped stylesheet, an owl whose CSS did not
  // arrive - a chunk that failed to load, a policy that blocked it - rendered as nothing at
  // all rather than degrading, and nothing at all is indistinguishable from a bug. The
  // attributes draw it as a line figure in currentColor, correct in either theme; the
  // stylesheet then colours it in, because a CSS rule beats a presentation attribute.
  let { size = 20 }: { size?: number } = $props();
</script>

<svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true" fill="none">
  <!-- ear tufts -->
  <path d="M14 16 L20 6 L25 14 Z" class="body" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
  <path d="M50 16 L44 6 L39 14 Z" class="body" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
  <!-- body -->
  <circle cx="32" cy="34" r="26" class="body" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
  <!-- glasses -->
  <circle cx="21" cy="27" r="9" class="lens" fill="none" stroke="currentColor" stroke-width="2.5" />
  <circle cx="43" cy="27" r="9" class="lens" fill="none" stroke="currentColor" stroke-width="2.5" />
  <line x1="30" y1="27" x2="34" y2="27" class="frame" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
  <line x1="12" y1="25" x2="8" y2="23" class="frame" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
  <line x1="52" y1="25" x2="56" y2="23" class="frame" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
  <!-- eyes -->
  <circle cx="21" cy="27" r="2.6" class="eye" fill="currentColor" />
  <circle cx="43" cy="27" r="2.6" class="eye" fill="currentColor" />
  <!-- beak -->
  <path d="M32 44 L28 38 L36 38 Z" class="beak" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" />
  <!-- open book -->
  <path d="M17 49 Q 24.5 45.5 32 49 Q 39.5 45.5 47 49 L47 57 Q 39.5 53.5 32 57 Q 24.5 53.5 17 57 Z" class="book" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
  <line x1="32" y1="49" x2="32" y2="57" class="frame" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
</svg>

<style>
  .body {
    fill: var(--accent-soft, #fef3c7);
    stroke: currentColor;
    stroke-width: 2.5;
    stroke-linejoin: round;
  }
  .lens {
    fill: var(--surface, #fff);
    stroke: var(--accent-ink, #78350f);
    stroke-width: 2.5;
  }
  .frame {
    stroke: var(--accent-ink, #78350f);
    stroke-width: 2.5;
    stroke-linecap: round;
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
  .book {
    fill: var(--surface, #fff);
    stroke: currentColor;
    stroke-width: 2.5;
    stroke-linejoin: round;
  }
</style>
