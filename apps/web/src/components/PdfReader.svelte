<script lang="ts">
  // Full-screen PDF reader. Nothing pdf.js ships until the overlay opens: the library and its
  // worker are dynamically imported on first open, the document is destroyed on close, and only
  // the current page is rendered (canvas + text layer for selection), so the closed path costs
  // zero bytes and paging keeps memory flat. Opened by [data-open-reader] clicks or ?read=1.

  import type { PDFDocumentProxy } from 'pdfjs-dist';
  import { Download, ExternalLink, Minus, Plus, Scaling, X } from 'lucide-svelte';

  import { lockBodyScroll, trapFocus } from '../lib/overlay';

  let {
    pdfUrl,
    title,
    initialOpen = false,
  }: { pdfUrl: string; title: string; initialOpen?: boolean } = $props();

  let open = $state(false);
  let loading = $state(false);
  let failed = $state(false);
  let pageNum = $state(1);
  let pageCount = $state(0);
  let scale = $state(0); // 0 = fit-width on next render
  let overlay: HTMLElement | undefined = $state();
  let canvas: HTMLCanvasElement | undefined = $state();
  let textLayer: HTMLDivElement | undefined = $state();
  let pageBox: HTMLDivElement | undefined = $state();

  let doc: PDFDocumentProxy | null = null;
  let pdfjs: typeof import('pdfjs-dist') | null = null;
  let previousFocus: Element | null = null;
  let unlockScroll: (() => void) | null = null;
  let renderSeq = 0;

  async function show() {
    previousFocus = document.activeElement;
    open = true;
    failed = false;
    unlockScroll = lockBodyScroll();
    const url = new URL(window.location.href);
    url.searchParams.set('read', '1');
    history.replaceState(null, '', url);
    if (doc === null) await load();
    else void renderPage();
  }

  function hide() {
    open = false;
    unlockScroll?.();
    unlockScroll = null;
    const url = new URL(window.location.href);
    url.searchParams.delete('read');
    history.replaceState(null, '', url);
    void doc?.destroy();
    doc = null;
    pageCount = 0;
    pageNum = 1;
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
      pageCount = doc.numPages;
      pageNum = 1;
      await renderPage();
    } catch {
      failed = true;
    } finally {
      loading = false;
    }
  }

  async function renderPage() {
    if (doc === null || pdfjs === null || !canvas || !textLayer) return;
    const seq = ++renderSeq;
    const page = await doc.getPage(pageNum);
    if (scale === 0 && pageBox) {
      // Fit-width on first render.
      const base = page.getViewport({ scale: 1 });
      scale = Math.min(2, Math.max(0.5, (pageBox.clientWidth - 32) / base.width));
    }
    const viewport = page.getViewport({ scale: scale || 1 });
    if (seq !== renderSeq) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(viewport.width * dpr);
    canvas.height = Math.floor(viewport.height * dpr);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    await page.render({ canvasContext: ctx, viewport }).promise;

    textLayer.replaceChildren();
    textLayer.style.width = `${viewport.width}px`;
    textLayer.style.height = `${viewport.height}px`;
    textLayer.style.setProperty('--scale-factor', String(viewport.scale));
    const layer = new pdfjs.TextLayer({
      textContentSource: page.streamTextContent(),
      container: textLayer,
      viewport,
    });
    await layer.render();
  }

  function goTo(target: number) {
    if (doc === null) return;
    pageNum = Math.min(Math.max(1, target), pageCount);
    void renderPage();
  }

  function zoom(delta: number) {
    scale = Math.min(3, Math.max(0.5, (scale || 1) + delta));
    void renderPage();
  }

  function fitWidth() {
    scale = 0;
    void renderPage();
  }

  function onDocumentClick(event: MouseEvent) {
    if ((event.target as Element).closest('[data-open-reader]')) {
      event.preventDefault();
      void show();
    }
  }

  function onWindowKeydown(event: KeyboardEvent) {
    if (!open) return;
    if (event.key === 'Escape') hide();
    else if (event.key === 'ArrowRight') goTo(pageNum + 1);
    else if (event.key === 'ArrowLeft') goTo(pageNum - 1);
  }

  function onOverlayKeydown(event: KeyboardEvent) {
    if (overlay) trapFocus(overlay, event);
  }

  $effect(() => {
    if (initialOpen && !open && doc === null && !failed) void show();
  });
</script>

<svelte:document onclick={onDocumentClick} />
<svelte:window onkeydown={onWindowKeydown} />

{#if open}
  <div
    class="overlay"
    role="dialog"
    aria-modal="true"
    aria-label={`Reading ${title}`}
    bind:this={overlay}
    onkeydown={onOverlayKeydown}
  >
    <header class="bar">
      <button class="tool" onclick={hide} aria-label="Close the reader">
        <X size={18} aria-hidden="true" />
      </button>
      <span class="doc-title">{title}</span>
      <div class="controls">
        <label class="pagectl">
          <input
            type="number"
            min="1"
            max={pageCount || 1}
            value={pageNum}
            aria-label="Page number"
            onchange={(event) => goTo(Number(event.currentTarget.value) || 1)}
          />
          <span>/ {pageCount || '?'}</span>
        </label>
        <button class="tool" onclick={() => zoom(-0.2)} aria-label="Zoom out">
          <Minus size={16} aria-hidden="true" />
        </button>
        <button class="tool" onclick={() => zoom(0.2)} aria-label="Zoom in">
          <Plus size={16} aria-hidden="true" />
        </button>
        <button class="tool" onclick={fitWidth} aria-label="Fit width">
          <Scaling size={16} aria-hidden="true" />
        </button>
        <a class="tool" href={pdfUrl} download aria-label="Download the PDF">
          <Download size={16} aria-hidden="true" />
        </a>
        <a
          class="tool"
          href={pdfUrl}
          target="_blank"
          rel="noopener"
          aria-label="Open the PDF in a new tab"
        >
          <ExternalLink size={16} aria-hidden="true" />
        </a>
      </div>
    </header>
    <div class="page-box" bind:this={pageBox}>
      {#if loading}
        <p class="status">Loading the PDF from arXiv…</p>
      {:else if failed}
        <p class="status">
          The PDF could not be loaded here — <a href={pdfUrl} rel="noopener">open it directly</a>.
        </p>
      {/if}
      <div class="page" class:hidden={loading || failed}>
        <canvas bind:this={canvas}></canvas>
        <div class="textLayer" bind:this={textLayer}></div>
      </div>
    </div>
  </div>
{/if}

<style>
  .overlay {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: flex;
    flex-direction: column;
    background: var(--bg, #faf7f1);
  }
  .bar {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.55rem 0.9rem;
    border-bottom: 1px solid var(--line, #e6e1d5);
    background: var(--surface, #fff);
  }
  .doc-title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--ink, #17191c);
    font-size: 0.9rem;
    font-weight: 600;
  }
  .controls {
    display: flex;
    align-items: center;
    gap: 0.25rem;
  }
  .tool {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 2rem;
    border: none;
    border-radius: var(--radius-sm, 10px);
    background: none;
    color: var(--muted, #5d6570);
    cursor: pointer;
    text-decoration: none;
  }
  .tool:hover {
    background: var(--surface-2, #f4f0e8);
    color: var(--ink, #17191c);
  }
  .pagectl {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    color: var(--muted, #5d6570);
    font-size: 0.85rem;
  }
  .pagectl input {
    width: 3.2rem;
    padding: 0.25rem 0.4rem;
    border: 1px solid var(--line, #e6e1d5);
    border-radius: var(--radius-sm, 10px);
    background: var(--surface, #fff);
    color: var(--ink, #17191c);
    font: inherit;
    font-size: 0.85rem;
    text-align: center;
  }
  .page-box {
    flex: 1;
    overflow: auto;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 1rem;
  }
  .status {
    margin: 3rem 0 0;
    color: var(--muted, #5d6570);
  }
  .page {
    position: relative;
    box-shadow: var(--shadow-md, 0 12px 32px rgb(23 25 28 / 0.12));
  }
  .page.hidden {
    visibility: hidden;
  }
  .page canvas {
    display: block;
    background: #fff;
  }
  /* Minimal pdf.js text layer: invisible glyph spans positioned over the canvas so text is
     selectable; --scale-factor is set per render. */
  .page :global(.textLayer) {
    position: absolute;
    inset: 0;
    overflow: hidden;
    line-height: 1;
  }
  .page :global(.textLayer span) {
    position: absolute;
    color: transparent;
    transform-origin: 0 0;
    white-space: pre;
    cursor: text;
  }
  .page :global(.textLayer ::selection) {
    background: rgb(37 99 235 / 0.3);
  }
</style>
