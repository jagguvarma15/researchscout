<script lang="ts">
  // Full-screen PDF reader.
  //
  // The document scrolls continuously, the way a reader expects, but only the pages near the
  // viewport carry a canvas - every other page is a correctly-sized placeholder, so the
  // scrollbar is honest from the first frame and memory does not grow with the paper. Which
  // pages those are is decided by lib/reader-pages, away from the DOM, where it is tested.
  //
  // Cancellation is the part to be careful with. Several pages can be rendering at once while
  // you scroll, and a render that outlives the page it was for will draw over a canvas that
  // now belongs to someone else. Every page therefore carries its own sequence number, and a
  // render checks it still holds the canvas after every await. Pages are also cancelled before
  // the document is destroyed, because destroying a document mid-render throws.
  //
  // Nothing pdf.js ships is loaded until the reader opens; the library and its worker are
  // imported dynamically on first open. Opened by [data-open-reader] clicks or ?read=1.

  import type { PDFDocumentProxy, RenderTask, TextLayer as TextLayerTask } from 'pdfjs-dist';
  import {
    ChevronLeft,
    ChevronRight,
    Download,
    ExternalLink,
    Highlighter,
    List,
    Maximize2,
    Minimize2,
    Minus,
    Plus,
    Scaling,
    Trash2,
    X,
  } from 'lucide-svelte';
  import { onDestroy, tick } from 'svelte';

  import {
    clampRect,
    highlightAt,
    inkBounds,
    loadHighlights,
    newId,
    rectFromDrag,
    saveHighlights,
    toScreenRects,
    type Highlight,
    type HighlightRect,
  } from '../lib/highlights';
  import { prefersReducedMotion } from '../lib/motion';
  import { lockBodyScroll, trapFocus } from '../lib/overlay';
  import {
    currentPage,
    layoutPages,
    scrollTopFor,
    totalHeight,
    visibleRange,
  } from '../lib/reader-pages';

  let {
    pdfUrl,
    title,
    paperId,
    initialOpen = false,
  }: { pdfUrl: string; title: string; paperId: string; initialOpen?: boolean } = $props();

  // Space between pages, and how many pages either side of the viewport stay drawn. One is
  // enough to cover a fast flick without holding four full-resolution bitmaps at once.
  const GAP = 16;
  const OVERSCAN = 1;
  const IDLE_MS = 2600;

  // Over a white page in both themes, so these are literal rather than tokens. Deliberately
  // weak: a mark should be a wash over the words, not a block on top of them.
  const COLORS: Record<string, string> = {
    yellow: 'rgb(250 204 21 / 0.22)',
    green: 'rgb(74 222 128 / 0.22)',
    pink: 'rgb(244 114 182 / 0.20)',
  };

  // How far a drag has to travel before it counts as framing something rather than a click,
  // and how much air is left around the ink once the frame snaps onto it. Both in page units.
  const DRAG_FLOOR = 4;
  const INK_PADDING = 1.5;

  let open = $state(false);
  let minimized = $state(false);
  let loading = $state(false);
  let failed = $state(false);
  let fullscreen = $state(false);
  let chromeOn = $state(true);
  let listOpen = $state(false);
  let page = $state(1);
  let scale = $state(0); // 0 = fit width at the next layout
  let baseSizes = $state<{ w: number; h: number }[]>([]);
  let highlights = $state<Highlight[]>([]);

  // Off by default: the reader is for reading, so the pointer behaves like a pointer and text
  // selects and copies as it would anywhere. Framing is something you switch on.
  let marking = $state(false);
  // A mark being framed, before a colour has been chosen for it, in page units.
  let pending = $state<{ page: number; rect: HighlightRect } | null>(null);
  let anchor = $state<{ x: number; y: number } | null>(null);
  let removing = $state<{ id: string; x: number; y: number } | null>(null);

  type Grip = 'new' | 'nw' | 'ne' | 'sw' | 'se';
  let dragging: Grip | null = $state(null);
  let dragStart: { page: number; x: number; y: number } | null = null;

  let overlay: HTMLElement | undefined = $state();
  let viewer: HTMLElement | undefined = $state();
  let content: HTMLElement | undefined = $state();
  let pageEls = $state<(HTMLElement | undefined)[]>([]);

  /**
   * How far the pages sit below the top of the scrollable area.
   *
   * Page positions from lib/reader-pages start at zero, but the scroller has padding above
   * them (to clear the floating title bar) and, while loading, a status line as well. Read
   * rather than assumed, because that second one comes and goes.
   */
  function contentOffset(): number {
    return content?.offsetTop ?? 0;
  }

  let doc: PDFDocumentProxy | null = null;
  let pdfjs: typeof import('pdfjs-dist') | null = null;
  let previousFocus: Element | null = null;
  let unlockScroll: (() => void) | null = null;
  let idleTimer: ReturnType<typeof setTimeout> | undefined;
  let scrollFrame = 0;
  let swipeX = 0;
  let swipeAt = 0;

  // Per page: the sequence number of the newest render, and the tasks it owns.
  const seqOf = new Map<number, number>();
  const live = new Map<number, { task: RenderTask | null; text: TextLayerTask | null }>();

  const effScale = $derived(scale || 1);
  const sizes = $derived(baseSizes.map((s) => ({ w: s.w * effScale, h: s.h * effScale })));
  const boxes = $derived(layoutPages(sizes.map((s) => s.h), GAP));
  const contentHeight = $derived(totalHeight(boxes));
  // Pages are centred at 50% of this, so it has to be at least as wide as the widest page:
  // otherwise zooming past the window puts half of each page left of zero, where no amount
  // of horizontal scrolling reaches it.
  const contentWidth = $derived(sizes.reduce((widest, s) => Math.max(widest, s.w), 0));
  const pageCount = $derived(baseSizes.length);
  const ordered = $derived([...highlights].sort((a, b) => a.page - b.page));

  // The chrome fades out while you read and comes back the moment you reach for it. Under
  // reduced motion it simply never hides - a control that vanishes is its own kind of motion.
  function wake() {
    chromeOn = true;
    clearTimeout(idleTimer);
    if (prefersReducedMotion() || pending || removing || listOpen || marking) return;
    idleTimer = setTimeout(() => (chromeOn = false), IDLE_MS);
  }

  async function show() {
    // Phones hand the paper to the native viewer: pinch zoom, share, and text selection all
    // work there, and this chrome's 30px tools and hover-gated wake do not. Every entrance -
    // the Read button, card links, a ?read=1 deep link - funnels through here, so this is
    // the one place the split lives. The query is cleaned off so reload does not re-trigger.
    if (window.matchMedia('(max-width: 40rem)').matches) {
      const url = new URL(window.location.href);
      url.searchParams.delete('read');
      history.replaceState({ ...history.state }, '', url);
      window.open(pdfUrl, '_blank', 'noopener');
      return;
    }
    previousFocus = document.activeElement;
    open = true;
    minimized = false;
    failed = false;
    unlockScroll = lockBodyScroll();
    highlights = loadHighlights(paperId);
    const url = new URL(window.location.href);
    url.searchParams.set('read', '1');
    // Spread, never null: the client router keeps its scroll position and entry index in
    // history.state, and replacing it with null breaks scroll restoration and forward
    // navigation for this entry.
    history.replaceState({ ...history.state }, '', url);
    wake();
    if (doc === null) await load();
    else await tick();
  }

  function releaseAll() {
    for (const num of [...live.keys()]) release(num);
  }

  // Navigation no longer reloads the document, so leaving this page destroys the island
  // without anyone having pressed close. Everything hide() releases - the scroll lock, the
  // live render tasks, the pdf.js document - would otherwise outlive it, and a scroll lock
  // set on a body that survives the swap means the next page cannot be scrolled at all.
  onDestroy(() => {
    if (open) hide();
  });

  function hide() {
    open = false;
    minimized = false;
    clearPending();
    removing = null;
    marking = false;
    listOpen = false;
    clearTimeout(idleTimer);
    unlockScroll?.();
    unlockScroll = null;
    if (document.fullscreenElement) void document.exitFullscreen();
    const url = new URL(window.location.href);
    url.searchParams.delete('read');
    // Spread for the same reason as in show(): the router's state must survive this.
    history.replaceState({ ...history.state }, '', url);
    // Cancel every live render before destroying: destroying a document mid-render throws,
    // and a render still awaiting getPage would resolve against a dead document.
    releaseAll();
    void doc?.destroy();
    doc = null;
    baseSizes = [];
    pageEls = [];
    page = 1;
    scale = 0;
    if (previousFocus instanceof HTMLElement) previousFocus.focus();
  }

  async function load() {
    loading = true;
    try {
      pdfjs = await import('pdfjs-dist');
      const worker = await import('pdfjs-dist/build/pdf.worker.min.mjs?url');
      pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
      doc = await pdfjs.getDocument({ url: pdfUrl }).promise;

      // One real page size, assumed for the rest, so the document has a full-length scrollbar
      // immediately. The remaining sizes are measured straight after and corrected in place;
      // in a paper they are almost always identical, so nothing visibly moves.
      const first = await doc.getPage(1);
      const base = first.getViewport({ scale: 1 });
      baseSizes = Array.from({ length: doc.numPages }, () => ({
        w: base.width,
        h: base.height,
      }));
      await tick();
      fitWidth();
      void measureRest();
    } catch {
      failed = true;
    } finally {
      loading = false;
    }
  }

  async function measureRest() {
    const current = doc;
    for (let num = 2; num <= (current?.numPages ?? 0); num += 1) {
      if (doc !== current) return; // The reader closed and reopened under us.
      const p = await current!.getPage(num);
      const view = p.getViewport({ scale: 1 });
      const known = baseSizes[num - 1];
      if (known && (known.w !== view.width || known.h !== view.height)) {
        baseSizes[num - 1] = { w: view.width, h: view.height };
      }
    }
  }

  function release(num: number) {
    // Bumping first invalidates any render still awaiting getPage for this page.
    seqOf.set(num, (seqOf.get(num) ?? 0) + 1);
    const running = live.get(num);
    running?.task?.cancel();
    running?.text?.cancel();
    live.delete(num);
    const el = pageEls[num - 1];
    const canvas = el?.querySelector('canvas');
    if (canvas) {
      // Zero width, not clearRect: this is what actually frees the backing store.
      canvas.width = 0;
      canvas.height = 0;
    }
    el?.querySelector<HTMLElement>('.textLayer')?.replaceChildren();
  }

  async function renderPage(num: number) {
    if (doc === null || pdfjs === null) return;
    const el = pageEls[num - 1];
    const canvas = el?.querySelector('canvas');
    const textLayer = el?.querySelector<HTMLElement>('.textLayer');
    if (!canvas || !textLayer) return;

    const seq = (seqOf.get(num) ?? 0) + 1;
    seqOf.set(num, seq);
    const entry: { task: RenderTask | null; text: TextLayerTask | null } = {
      task: null,
      text: null,
    };
    live.set(num, entry);
    const holds = () => seqOf.get(num) === seq && live.get(num) === entry;

    const p = await doc.getPage(num);
    if (!holds()) return;
    const viewport = p.getViewport({ scale: effScale });
    // Capped: at fit width on a large display an uncapped ratio makes each page a bitmap of
    // tens of megabytes, and this runs on a machine with 8GB to share.
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.floor(viewport.width * dpr);
    canvas.height = Math.floor(viewport.height * dpr);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    entry.task = p.render({ canvasContext: ctx, viewport });
    try {
      await entry.task.promise;
    } catch (error) {
      // A newer render cancelled this one mid-draw; that render owns the canvas now.
      if (error instanceof pdfjs.RenderingCancelledException) return;
      throw error;
    }
    entry.task = null;
    if (!holds()) return;

    textLayer.replaceChildren();
    textLayer.style.width = `${viewport.width}px`;
    textLayer.style.height = `${viewport.height}px`;
    textLayer.style.setProperty('--scale-factor', String(viewport.scale));
    entry.text = new pdfjs.TextLayer({
      textContentSource: p.streamTextContent(),
      container: textLayer,
      viewport,
    });
    try {
      await entry.text.render();
    } catch {
      // Cancelled text layers reject; only the live render's failure is worth reporting, and
      // a page without selectable text is still a readable page.
      return;
    }
    entry.text = null;
  }

  function syncRendered() {
    if (!viewer || boxes.length === 0) return;
    const top = viewer.scrollTop - contentOffset();
    const { first, last } = visibleRange(boxes, top, viewer.clientHeight, OVERSCAN);
    for (const num of [...live.keys()]) {
      if (num - 1 < first || num - 1 > last) release(num);
    }
    for (let i = first; i <= last; i += 1) {
      if (!live.has(i + 1)) void renderPage(i + 1);
    }
  }

  function onScroll() {
    if (scrollFrame) return;
    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = 0;
      if (!viewer) return;
      page = currentPage(boxes, viewer.scrollTop - contentOffset(), viewer.clientHeight);
      syncRendered();
    });
  }

  /**
   * Turning one page glides, because that is the page transition; jumping ten does not,
   * because a smooth scroll across ten pages renders every page it passes through and takes
   * a second to arrive somewhere you asked to be immediately.
   */
  function goTo(target: number, behavior: ScrollBehavior = 'auto') {
    if (!viewer || boxes.length === 0) return;
    page = Math.min(Math.max(1, target), pageCount);
    viewer.scrollTo({ top: scrollTopFor(boxes, page) + contentOffset(), behavior });
    // A smooth scroll has not moved yet, so the range would be computed against where we
    // were; the scroll handler covers it as the animation runs.
    if (behavior === 'auto') syncRendered();
  }

  function step(delta: number) {
    goTo(page + delta, prefersReducedMotion() ? 'auto' : 'smooth');
  }

  async function rescale(next: number) {
    const anchor = page;
    scale = Math.min(3, Math.max(0.4, next));
    releaseAll();
    // Wait for the new box heights to reach the DOM before scrolling to the anchor, or the
    // scroll lands against the old layout and the page under you changes.
    await tick();
    goTo(anchor);
  }

  function zoom(delta: number) {
    void rescale(effScale + delta);
  }

  function fitWidth() {
    if (!viewer || baseSizes.length === 0) return;
    const usable = viewer.clientWidth - 48;
    void rescale(Math.min(2, Math.max(0.4, usable / baseSizes[0].w)));
  }

  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await overlay?.requestFullscreen();
    } catch {
      // Refused by the browser (no user gesture, or disallowed); the reader is unaffected.
    }
  }

  function toggleMinimized() {
    minimized = !minimized;
    clearPending();
    removing = null;
    listOpen = false;
    if (minimized) {
      unlockScroll?.();
      unlockScroll = null;
    } else {
      unlockScroll = lockBodyScroll();
      wake();
    }
  }

  // Highlights ---------------------------------------------------------------

  function persist() {
    saveHighlights(paperId, highlights);
  }

  function clearPending() {
    pending = null;
    anchor = null;
    dragStart = null;
    dragging = null;
  }

  /** Where a page's own coordinates put a pointer, at scale 1 so it matches what is stored. */
  function pagePoint(event: { clientX: number; clientY: number }, num: number) {
    const el = pageEls[num - 1];
    if (!el) return null;
    const box = el.getBoundingClientRect();
    return { x: (event.clientX - box.left) / effScale, y: (event.clientY - box.top) / effScale };
  }

  // Framing -------------------------------------------------------------------

  function anchorPending() {
    if (!pending) return;
    const el = pageEls[pending.page - 1];
    if (!el) return;
    const box = el.getBoundingClientRect();
    anchor = {
      x: box.left + (pending.rect.x + pending.rect.w / 2) * effScale,
      y: box.top + pending.rect.y * effScale,
    };
  }

  /**
   * Pulls a loosely drawn frame in onto whatever is actually drawn inside it.
   *
   * The canvas is the source of truth rather than the text layer, because on a canvas a
   * paragraph, an equation and a plot all look the same - one rule covers every kind of thing
   * on a page, which is the whole reason this replaced selecting text.
   *
   * A frame over blank paper is left as drawn; there is nothing to snap onto, and silently
   * collapsing it to nothing would look like the drag was lost.
   */
  function snapToInk(num: number, rect: HighlightRect): HighlightRect {
    const canvas = pageEls[num - 1]?.querySelector('canvas');
    if (!canvas || canvas.width === 0) return rect;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) return rect;
    // Canvas pixels are page units times the render scale times the device ratio, and the
    // ratio is whatever the canvas ended up with rather than whatever the window reports now.
    const ratio = canvas.width / (baseSizes[num - 1].w * effScale);
    const px = Math.max(0, Math.round(rect.x * effScale * ratio));
    const py = Math.max(0, Math.round(rect.y * effScale * ratio));
    const pw = Math.min(Math.round(rect.w * effScale * ratio), canvas.width - px);
    const ph = Math.min(Math.round(rect.h * effScale * ratio), canvas.height - py);
    if (pw <= 0 || ph <= 0) return rect;

    let bounds: ReturnType<typeof inkBounds>;
    try {
      const patch = context.getImageData(px, py, pw, ph);
      bounds = inkBounds(patch.data, pw, ph);
    } catch {
      return rect; // A tainted or oversized read; the frame stands as drawn.
    }
    if (!bounds) return rect;

    const unit = effScale * ratio;
    const size = baseSizes[num - 1];
    return clampRect(
      {
        x: rect.x + bounds.x / unit - INK_PADDING,
        y: rect.y + bounds.y / unit - INK_PADDING,
        w: bounds.w / unit + INK_PADDING * 2,
        h: bounds.h / unit + INK_PADDING * 2,
      },
      size.w,
      size.h,
    );
  }

  /** Whatever text falls inside a frame, so the highlights list can name it. */
  function textInside(num: number, rect: HighlightRect): string {
    const el = pageEls[num - 1];
    const layer = el?.querySelector('.textLayer');
    if (!el || !layer) return '';
    const pageBox = el.getBoundingClientRect();
    const left = rect.x * effScale;
    const top = rect.y * effScale;
    const right = left + rect.w * effScale;
    const bottom = top + rect.h * effScale;
    const words: string[] = [];
    for (const span of layer.querySelectorAll('span')) {
      const box = span.getBoundingClientRect();
      const midX = box.left + box.width / 2 - pageBox.left;
      const midY = box.top + box.height / 2 - pageBox.top;
      if (midX >= left && midX <= right && midY >= top && midY <= bottom) {
        words.push(span.textContent ?? '');
      }
    }
    return words.join(' ').replace(/\s+/g, ' ').trim();
  }

  function onPagePointerDown(event: PointerEvent, num: number) {
    if (!marking || minimized || event.button !== 0) return;
    event.preventDefault();
    const point = pagePoint(event, num);
    if (!point) return;
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    dragStart = { page: num, ...point };
    dragging = 'new';
    removing = null;
    anchor = null;
    pending = { page: num, rect: { x: point.x, y: point.y, w: 0, h: 0 } };
  }

  function onPagePointerMove(event: PointerEvent) {
    if (dragging !== 'new' || !dragStart) return;
    const point = pagePoint(event, dragStart.page);
    if (!point) return;
    const size = baseSizes[dragStart.page - 1];
    pending = {
      page: dragStart.page,
      rect: clampRect(rectFromDrag(dragStart, point), size.w, size.h),
    };
  }

  function onPagePointerUp(event: PointerEvent) {
    if (dragging !== 'new') return;
    (event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId);
    dragging = null;
    dragStart = null;
    if (!pending) return;

    // Barely moved: that was a click, not a frame, and the click handler deals with it.
    if (pending.rect.w < DRAG_FLOOR || pending.rect.h < DRAG_FLOOR) {
      pending = null;
      return;
    }

    pending = { page: pending.page, rect: snapToInk(pending.page, pending.rect) };
    anchorPending();
    chromeOn = true;
  }

  /**
   * Clicking a mark offers to remove it, in either mode - reaching for the highlighter first
   * would be a strange thing to have to do in order to undo one.
   */
  function onPageClick(event: MouseEvent, num: number) {
    if (dragging || pending) return;
    if (!window.getSelection()?.isCollapsed) return; // A selection just ended here.
    const el = pageEls[num - 1];
    if (!el) return;
    const box = el.getBoundingClientRect();
    const hit = highlightAt(
      highlights,
      num,
      event.clientX - box.left,
      event.clientY - box.top,
      effScale,
    );
    removing = hit ? { id: hit.id, x: event.clientX, y: event.clientY } : null;
  }

  function toggleMarking() {
    marking = !marking;
    clearPending();
    removing = null;
    wake();
  }

  // Grips ---------------------------------------------------------------------

  function onGripDown(grip: Grip, event: PointerEvent) {
    event.preventDefault();
    event.stopPropagation();
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    dragging = grip;
  }

  function onGripMove(event: PointerEvent) {
    if (!dragging || dragging === 'new' || !pending) return;
    const point = pagePoint(event, pending.page);
    if (!point) return;
    const rect = pending.rect;
    // The corner opposite the one being dragged is the one that stays put.
    const fixed = {
      x: dragging === 'nw' || dragging === 'sw' ? rect.x + rect.w : rect.x,
      y: dragging === 'nw' || dragging === 'ne' ? rect.y + rect.h : rect.y,
    };
    const size = baseSizes[pending.page - 1];
    // No snapping here: adjusting a corner by hand is an explicit instruction, and having it
    // spring back onto the ink would make the corner impossible to place.
    pending = { page: pending.page, rect: clampRect(rectFromDrag(fixed, point), size.w, size.h) };
    anchorPending();
  }

  function onGripUp(event: PointerEvent) {
    (event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId);
    dragging = null;
  }

  // Committing and removing ---------------------------------------------------

  function applyHighlight(color: string) {
    if (!pending) return;
    highlights.push({
      id: newId(Date.now()),
      page: pending.page,
      color,
      text: textInside(pending.page, pending.rect),
      rects: [pending.rect],
    });
    persist();
    clearPending();
    wake();
  }

  function removeHighlight(id: string) {
    highlights = highlights.filter((item) => item.id !== id);
    persist();
    removing = null;
  }

  // Input --------------------------------------------------------------------

  function isEditable(target: EventTarget | null): boolean {
    return (
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target instanceof HTMLSelectElement ||
      (target instanceof HTMLElement && target.isContentEditable)
    );
  }

  function onWindowKeydown(event: KeyboardEvent) {
    if (!open) return;
    wake();
    if (event.key === 'Escape') {
      if (pending) clearPending();
      else if (removing) removing = null;
      else if (marking) marking = false;
      else if (listOpen) listOpen = false;
      else if (!document.fullscreenElement) hide();
      return;
    }
    if (minimized || isEditable(event.target)) return;
    if (event.key === 'ArrowRight' || event.key === 'PageDown') {
      event.preventDefault();
      step(1);
    } else if (event.key === 'ArrowLeft' || event.key === 'PageUp') {
      event.preventDefault();
      step(-1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      goTo(1);
    } else if (event.key === 'End') {
      event.preventDefault();
      goTo(pageCount);
    }
  }

  function onWheel(event: WheelEvent) {
    // Pinch on a trackpad arrives as a wheel event with ctrlKey set; the browser would
    // otherwise zoom the whole page, which in a full-screen reader is never what was meant.
    if (event.ctrlKey) {
      event.preventDefault();
      zoom(-event.deltaY * 0.01);
      return;
    }
    // Two-finger horizontal swipe turns the page. The accumulator plus the cooldown is what
    // makes one gesture one page rather than a fan of them.
    if (Math.abs(event.deltaX) <= Math.abs(event.deltaY)) return;
    const now = Date.now();
    if (now - swipeAt > 400) swipeX = 0;
    swipeX += event.deltaX;
    if (Math.abs(swipeX) < 120) return;
    step(swipeX > 0 ? 1 : -1);
    swipeX = 0;
    swipeAt = now;
  }

  function onDocumentClick(event: MouseEvent) {
    if ((event.target as Element).closest('[data-open-reader]')) {
      event.preventDefault();
      void show();
    }
  }

  function onOverlayKeydown(event: KeyboardEvent) {
    if (overlay && !minimized) trapFocus(overlay, event);
  }

  function onFullscreenChange() {
    fullscreen = document.fullscreenElement !== null;
  }

  $effect(() => {
    if (initialOpen && !open && doc === null && !failed) void show();
  });

  $effect(() => {
    // Re-derive what is on screen whenever the layout changes underneath it - a corrected
    // page size from measureRest, or a zoom.
    void boxes.length;
    void effScale;
    if (open && !minimized) syncRendered();
  });
</script>

<svelte:document onclick={onDocumentClick} onfullscreenchange={onFullscreenChange} />
<svelte:window onkeydown={onWindowKeydown} />

{#if open}
  <div
    class="overlay"
    class:minimized
    class:idle={!chromeOn && !minimized && !loading && !failed}
    role="dialog"
    aria-modal={!minimized}
    aria-label={`Reading ${title}`}
    bind:this={overlay}
    onkeydown={onOverlayKeydown}
    onmousemove={wake}
  >
    {#if minimized}
      <button class="dock" onclick={toggleMinimized}>
        <span class="dock-title">{title}</span>
        <span class="dock-page">{page} / {pageCount || '?'}</span>
        <Maximize2 size={15} aria-hidden="true" />
      </button>
    {:else}
      <div class="chrome top">
        <span class="doc-title">{title}</span>
        <div class="window-controls">
          <button class="tool" onclick={toggleMinimized} aria-label="Minimize the reader">
            <Minimize2 size={15} aria-hidden="true" />
          </button>
          <button
            class="tool"
            onclick={toggleFullscreen}
            aria-label={fullscreen ? 'Leave full screen' : 'Fill the screen'}
          >
            <Maximize2 size={15} aria-hidden="true" />
          </button>
          <button class="tool" onclick={hide} aria-label="Close the reader">
            <X size={16} aria-hidden="true" />
          </button>
        </div>
      </div>

      <div
        class="viewer"
        class:marking
        bind:this={viewer}
        onscroll={onScroll}
        onwheel={onWheel}
      >
        {#if loading}
          <p class="status">Loading the PDF from arXiv…</p>
        {:else if failed}
          <p class="status">
            The PDF could not be loaded here — <a href={pdfUrl} rel="noopener">open it directly</a>.
          </p>
        {/if}
        <div
          class="content"
          bind:this={content}
          style={`height:${contentHeight}px;min-width:${contentWidth}px`}
        >
          {#each sizes as size, index}
            <div
              class="pdfpage"
              bind:this={pageEls[index]}
              style={`top:${boxes[index]?.top ?? 0}px;width:${size.w}px;height:${size.h}px`}
              onclick={(event) => onPageClick(event, index + 1)}
              onpointerdown={(event) => onPagePointerDown(event, index + 1)}
              onpointermove={onPagePointerMove}
              onpointerup={onPagePointerUp}
              role="presentation"
            >
              <canvas></canvas>
              <div class="hl" aria-hidden="true">
                {#each highlights.filter((item) => item.page === index + 1) as item}
                  {#each toScreenRects(item.rects, effScale) as rect}
                    <span
                      style={`left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px;background:${COLORS[item.color] ?? COLORS.yellow}`}
                    ></span>
                  {/each}
                {/each}
              </div>
              <div class="textLayer"></div>
              {#if pending && pending.page === index + 1}
                {@const box = toScreenRects([pending.rect], effScale)[0]}
                <div
                  class="marquee"
                  style={`left:${box.left}px;top:${box.top}px;width:${box.width}px;height:${box.height}px`}
                >
                  <!-- Corner grips, so a frame that came out slightly wrong is corrected
                       rather than redrawn. Each one pivots on the corner opposite it. -->
                  {#each ['nw', 'ne', 'sw', 'se'] as const as corner}
                    <button
                      class={`grip corner ${corner}`}
                      onpointerdown={(event) => onGripDown(corner, event)}
                      onpointermove={onGripMove}
                      onpointerup={onGripUp}
                      aria-label={`Adjust the ${corner} corner`}
                    ></button>
                  {/each}
                </div>
              {/if}
            </div>
          {/each}
        </div>
      </div>

      <!-- Edge pointers: large hover targets rather than small buttons, because turning the
           page is the thing you do most and it should not need aim. -->
      <button
        class="edge left"
        onclick={() => step(-1)}
        disabled={page <= 1}
        aria-label="Previous page"
      >
        <ChevronLeft size={26} aria-hidden="true" />
      </button>
      <button
        class="edge right"
        onclick={() => step(1)}
        disabled={pageCount === 0 || page >= pageCount}
        aria-label="Next page"
      >
        <ChevronRight size={26} aria-hidden="true" />
      </button>

      <div class="chrome bottom">
        <button class="tool" onclick={() => zoom(-0.2)} aria-label="Zoom out">
          <Minus size={15} aria-hidden="true" />
        </button>
        <span class="readout">{Math.round(effScale * 100)}%</span>
        <button class="tool" onclick={() => zoom(0.2)} aria-label="Zoom in">
          <Plus size={15} aria-hidden="true" />
        </button>
        <button class="tool" onclick={fitWidth} aria-label="Fit the page width">
          <Scaling size={15} aria-hidden="true" />
        </button>
        <span class="sep"></span>
        <label class="pagectl">
          <input
            type="number"
            min="1"
            max={pageCount || 1}
            value={page}
            aria-label="Page number"
            onchange={(event) => goTo(Number(event.currentTarget.value) || 1)}
          />
          <span>/ {pageCount || '?'}</span>
        </label>
        <span class="sep"></span>
        <button
          class="tool"
          class:on={marking}
          onclick={toggleMarking}
          aria-pressed={marking}
          aria-label="Highlight"
          title="Drag a box over anything to highlight it"
        >
          <Highlighter size={15} aria-hidden="true" />
        </button>
        <button
          class="tool"
          class:on={listOpen}
          onclick={() => {
            listOpen = !listOpen;
            wake();
          }}
          aria-expanded={listOpen}
          aria-label={`Highlights (${highlights.length})`}
        >
          <List size={15} aria-hidden="true" />
          {#if highlights.length > 0}<span class="count">{highlights.length}</span>{/if}
        </button>
        <a class="tool" href={pdfUrl} download aria-label="Download the PDF">
          <Download size={15} aria-hidden="true" />
        </a>
        <a
          class="tool"
          href={pdfUrl}
          target="_blank"
          rel="noopener"
          aria-label="Open the PDF in a new tab"
        >
          <ExternalLink size={15} aria-hidden="true" />
        </a>
      </div>

      {#if listOpen}
        <aside class="list" aria-label="Highlights">
          <h2>Highlights</h2>
          {#if ordered.length === 0}
            <p class="listnote">
              Switch on the highlighter, then drag a box over anything on a page - a sentence,
              an equation, a figure - and it snaps onto what is inside. Highlights are kept in
              this browser, so they stay on this device.
            </p>
          {:else}
            <ul>
              {#each ordered as item}
                <li>
                  <button
                    class="jump"
                    onclick={() => {
                      goTo(item.page);
                      listOpen = false;
                    }}
                  >
                    <span class="swatch" style={`background:${COLORS[item.color]}`}></span>
                    <span class="quote" class:framed={!item.text}>
                      {item.text || 'Figure or equation'}
                    </span>
                    <span class="onpage">p{item.page}</span>
                  </button>
                  <button
                    class="tool"
                    onclick={() => removeHighlight(item.id)}
                    aria-label="Remove this highlight"
                  >
                    <Trash2 size={14} aria-hidden="true" />
                  </button>
                </li>
              {/each}
            </ul>
          {/if}
        </aside>
      {/if}

      {#if pending && anchor && !dragging}
        <div class="popover" style={`left:${anchor.x}px;top:${anchor.y}px`}>
          {#each Object.entries(COLORS) as [name, value]}
            <button
              class="swatch pick"
              style={`background:${value}`}
              onclick={() => applyHighlight(name)}
              aria-label={`Highlight in ${name}`}
            ></button>
          {/each}
        </div>
      {/if}

      {#if removing}
        <div class="popover" style={`left:${removing.x}px;top:${removing.y}px`}>
          <button class="remove" onclick={() => removeHighlight(removing.id)}>
            <Trash2 size={13} aria-hidden="true" />
            Remove
          </button>
        </div>
      {/if}
    {/if}
  </div>
{/if}

<style>
  .overlay {
    position: fixed;
    inset: 0;
    z-index: var(--z-reader, 50);
    background: var(--bg, #faf7f1);
  }
  .overlay.minimized {
    inset: auto 1.25rem 1.25rem auto;
    background: none;
  }

  /* Chrome floats over the page rather than taking a band of its own, and steps out of the
     way while you read. It never blocks a click it is not on. */
  .chrome {
    position: absolute;
    z-index: 3;
    display: flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.4rem 0.5rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-full, 999px);
    background: var(--surface, #fff);
    box-shadow: var(--shadow-lg, 0 2px 4px rgb(23 25 28 / 0.06), 0 16px 48px rgb(23 25 28 / 0.16));
    transition: opacity var(--dur-slow, 0.25s) var(--ease-out, cubic-bezier(0.2, 0, 0, 1));
  }
  .chrome.top {
    top: 0.75rem;
    left: 50%;
    transform: translateX(-50%);
    max-width: min(46rem, calc(100vw - 2rem));
    padding-left: 1rem;
  }
  .chrome.bottom {
    bottom: 0.9rem;
    left: 50%;
    transform: translateX(-50%);
    max-width: calc(100vw - 2rem);
    flex-wrap: wrap;
    justify-content: center;
  }
  .overlay.idle .chrome,
  .overlay.idle .edge {
    opacity: 0;
    pointer-events: none;
  }
  .chrome:hover,
  .chrome:focus-within {
    opacity: 1 !important;
    pointer-events: auto !important;
  }
  .doc-title {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--ink, #17191c);
    font-size: 0.85rem;
    font-weight: 600;
  }
  .window-controls {
    display: flex;
    gap: 0.15rem;
    margin-left: 0.5rem;
  }
  .tool {
    position: relative;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.2rem;
    width: 1.9rem;
    height: 1.9rem;
    flex-shrink: 0;
    border: none;
    border-radius: var(--radius-full, 999px);
    background: none;
    color: var(--muted, #5d6570);
    cursor: pointer;
    text-decoration: none;
  }
  .tool:hover {
    background: var(--surface-2, #f4f0e8);
    color: var(--ink, #17191c);
  }
  .tool.on {
    background: var(--accent-soft, #fef3c7);
    color: var(--accent-ink, #78350f);
  }
  .tool:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .count {
    position: absolute;
    top: -0.1rem;
    right: -0.1rem;
    min-width: 0.95rem;
    padding: 0 0.2rem;
    border-radius: 999px;
    background: var(--accent, #c2410c);
    color: var(--accent-contrast, #fff);
    font-size: 0.6rem;
    font-weight: 650;
    line-height: 0.95rem;
  }
  .readout {
    min-width: 3rem;
    text-align: center;
    color: var(--muted, #5d6570);
    font-size: 0.78rem;
    font-variant-numeric: tabular-nums;
  }
  .sep {
    width: 1px;
    height: 1.2rem;
    margin: 0 0.25rem;
    background: var(--line, #e6e1d5);
  }
  .pagectl {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    color: var(--muted, #5d6570);
    font-size: 0.8rem;
  }
  .pagectl input {
    width: 2.9rem;
    padding: 0.2rem 0.3rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-sm, 10px);
    background: var(--surface, #fff);
    color: var(--ink, #17191c);
    font: inherit;
    font-size: 0.8rem;
    text-align: center;
  }

  /* Top padding clears the floating title bar, so the first page opens below it rather than
     under it; the bottom clears the toolbar the same way. */
  .viewer {
    position: absolute;
    inset: 0;
    overflow: auto;
    overscroll-behavior: contain;
    padding: 4rem 0 5rem;
  }
  @media (prefers-reduced-motion: reduce) {
    .chrome {
      transition: none;
    }
  }
  .content {
    position: relative;
    margin: 0 auto;
    width: 100%;
  }
  /* Absolutely placed from the same boxes the render window is computed from, so what is
     drawn and what is laid out cannot disagree. */
  .pdfpage {
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    background: #fff;
    box-shadow: var(--shadow-md, 0 4px 12px rgb(23 25 28 / 0.08), 0 12px 32px rgb(23 25 28 / 0.1));
  }
  .pdfpage canvas {
    display: block;
  }
  .status {
    margin: 3rem 0 0;
    text-align: center;
    color: var(--muted, #5d6570);
  }

  /* Highlights sit over the canvas and under the text layer, so selecting still works. */
  .hl {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }
  .hl span {
    position: absolute;
    border-radius: 2px;
    mix-blend-mode: multiply;
  }
  /* Only while the highlighter is on does a drag frame a region; the text layer stops taking
     pointer events so the drag is not swallowed as a selection. With it off the page behaves
     like a page - an I-beam over text, which selects and copies as it would anywhere. */
  .viewer.marking .pdfpage {
    cursor: crosshair;
  }
  .viewer.marking :global(.textLayer) {
    pointer-events: none;
  }
  .marquee {
    position: absolute;
    border: 1.5px dashed var(--accent, #c2410c);
    background: rgb(194 65 12 / 0.12);
    border-radius: 3px;
  }
  .grip {
    position: absolute;
    padding: 0;
    border: 2px solid var(--surface, #fff);
    border-radius: 999px;
    background: var(--accent, #c2410c);
    box-shadow: var(--shadow-sm, 0 1px 2px rgb(23 25 28 / 0.04));
    cursor: grab;
    touch-action: none;
  }
  .grip:active {
    cursor: grabbing;
  }
  .grip.corner {
    width: 0.7rem;
    height: 0.7rem;
  }
  .grip.corner.nw {
    left: -0.35rem;
    top: -0.35rem;
    cursor: nwse-resize;
  }
  .grip.corner.ne {
    right: -0.35rem;
    top: -0.35rem;
    cursor: nesw-resize;
  }
  .grip.corner.sw {
    left: -0.35rem;
    bottom: -0.35rem;
    cursor: nesw-resize;
  }
  .grip.corner.se {
    right: -0.35rem;
    bottom: -0.35rem;
    cursor: nwse-resize;
  }

  .edge {
    position: absolute;
    top: 20%;
    bottom: 20%;
    width: 5rem;
    z-index: 2;
    display: flex;
    align-items: center;
    justify-content: center;
    border: none;
    background: none;
    color: var(--muted, #5d6570);
    cursor: pointer;
    opacity: 0;
    transition: opacity var(--dur-fast, 0.15s) var(--ease-out, cubic-bezier(0.2, 0, 0, 1));
  }
  .edge.left {
    left: 0;
    justify-content: flex-start;
    padding-left: 0.5rem;
  }
  .edge.right {
    right: 0;
    justify-content: flex-end;
    padding-right: 0.5rem;
  }
  .edge:hover:not(:disabled),
  .edge:focus-visible {
    opacity: 1;
  }
  .edge:disabled {
    cursor: default;
  }
  .edge :global(svg) {
    padding: 0.35rem;
    border-radius: 999px;
    background: var(--surface, #fff);
    box-shadow: var(--shadow-md, 0 4px 12px rgb(23 25 28 / 0.08), 0 12px 32px rgb(23 25 28 / 0.1));
  }

  .dock {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    max-width: min(22rem, calc(100vw - 2.5rem));
    padding: 0.55rem 0.8rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-full, 999px);
    background: var(--surface, #fff);
    color: var(--ink, #17191c);
    font: inherit;
    font-size: 0.82rem;
    box-shadow: var(--shadow-lg, 0 2px 4px rgb(23 25 28 / 0.06), 0 16px 48px rgb(23 25 28 / 0.16));
    cursor: pointer;
  }
  .dock-title {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 600;
  }
  .dock-page {
    flex-shrink: 0;
    color: var(--muted, #5d6570);
    font-variant-numeric: tabular-nums;
  }

  .popover {
    position: fixed;
    z-index: 4;
    display: flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.3rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-full, 999px);
    background: var(--surface, #fff);
    box-shadow: var(--shadow-lg, 0 2px 4px rgb(23 25 28 / 0.06), 0 16px 48px rgb(23 25 28 / 0.16));
    transform: translate(-50%, -130%);
  }
  .swatch {
    width: 1.35rem;
    height: 1.35rem;
    border-radius: 999px;
    border: 1px solid var(--line, #e6e1d5);
  }
  .swatch.pick {
    cursor: pointer;
    padding: 0;
  }
  .swatch.pick:hover {
    border-color: var(--ink, #17191c);
  }
  .remove {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.2rem 0.6rem;
    border: none;
    border-radius: 999px;
    background: none;
    color: var(--danger-ink, #7f1d1d);
    font: inherit;
    font-size: 0.78rem;
    cursor: pointer;
  }
  .remove:hover {
    background: var(--danger-soft, #fee2e2);
  }

  .list {
    position: absolute;
    right: 1rem;
    bottom: 4.2rem;
    z-index: 4;
    width: min(22rem, calc(100vw - 2rem));
    max-height: 60dvh;
    overflow-y: auto;
    padding: 0.9rem 1rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-md, 14px);
    background: var(--surface, #fff);
    box-shadow: var(--shadow-lg, 0 2px 4px rgb(23 25 28 / 0.06), 0 16px 48px rgb(23 25 28 / 0.16));
  }
  .list h2 {
    margin: 0 0 0.6rem;
    color: var(--muted, #5d6570);
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .listnote {
    margin: 0;
    color: var(--muted, #5d6570);
    font-size: 0.82rem;
  }
  .list ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .list li {
    display: flex;
    align-items: center;
    gap: 0.3rem;
  }
  .jump {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.4rem 0.45rem;
    border: none;
    border-radius: var(--radius-sm, 10px);
    background: none;
    color: var(--ink, #17191c);
    font: inherit;
    font-size: 0.82rem;
    text-align: left;
    cursor: pointer;
  }
  .jump:hover {
    background: var(--surface-2, #f4f0e8);
  }
  .jump .swatch {
    width: 0.75rem;
    height: 0.75rem;
    flex-shrink: 0;
  }
  .quote {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .quote.framed {
    color: var(--muted, #5d6570);
    font-style: italic;
  }
  .onpage {
    flex-shrink: 0;
    color: var(--muted, #5d6570);
    font-size: 0.72rem;
    font-variant-numeric: tabular-nums;
  }

  /* Minimal pdf.js text layer: invisible glyph spans positioned over the canvas so text is
     selectable; --scale-factor is set per render. */
  .pdfpage :global(.textLayer) {
    position: absolute;
    inset: 0;
    overflow: hidden;
    line-height: 1;
  }
  .pdfpage :global(.textLayer span) {
    position: absolute;
    color: transparent;
    transform-origin: 0 0;
    white-space: pre;
    cursor: text;
  }
  /* The alpha has to be in the colour. ::selection takes background-color, color and a few
     text properties and nothing else - an `opacity` beside it is ignored, which turns this
     into a solid block painted over the words you are trying to read. */
  .pdfpage :global(.textLayer ::selection) {
    background: rgb(194 65 12 / 0.28);
    color: transparent;
  }
</style>
