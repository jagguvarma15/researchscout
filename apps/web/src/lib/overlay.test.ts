// The outside-click judgement. The interesting case is the one that shipped as a bug: the
// clicked row is re-rendered away between its own handler and the document-level one, so
// containment checked at handler time reads a detached node and reports "outside" for the
// very element that was clicked.

import { describe, expect, it } from 'vitest';

import { clickedOutside, lockBodyScroll } from './overlay';

describe('clickedOutside', () => {
  it('reports a click elsewhere as outside', () => {
    const root = document.createElement('div');
    const elsewhere = document.createElement('button');
    const fake = { composedPath: () => [elsewhere, document.body, document] } as unknown as Event;
    expect(clickedOutside(fake, root)).toBe(true);
  });

  it('reports a click on a child as inside', () => {
    const root = document.createElement('div');
    const row = document.createElement('li');
    const fake = { composedPath: () => [row, root, document.body, document] } as unknown as Event;
    expect(clickedOutside(fake, root)).toBe(false);
  });

  it('never judges outside before the root is mounted', () => {
    const fake = { composedPath: () => [document.body, document] } as unknown as Event;
    expect(clickedOutside(fake, undefined)).toBe(false);
  });

  it('stays inside even when the row is unmounted between handlers', () => {
    const root = document.createElement('div');
    const row = document.createElement('button');
    root.appendChild(row);
    document.body.appendChild(root);

    let judged: boolean | null = null;
    // What a framework re-render does when the click's own handler shrinks the list.
    row.addEventListener('click', () => row.remove());
    document.addEventListener('click', (event) => (judged = clickedOutside(event, root)), {
      once: true,
    });
    row.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(root.contains(row)).toBe(false); // the node really is detached by then
    expect(judged).toBe(false); // and the composed path still knows where the click landed
    root.remove();
  });
});

// The scroll lock is shared state: overlapping overlays (drawer over the open search panel)
// each acquire it, and the styles must survive until the LAST one releases. The old
// per-caller save/restore captured "hidden" as the second overlay's previous value and
// restored it, stranding the page unscrollable.
describe('lockBodyScroll', () => {
  const root = () => document.documentElement;

  it('locks html and body and restores both on release', () => {
    root().style.overflow = '';
    document.body.style.overflow = '';
    const unlock = lockBodyScroll();
    expect(root().style.overflow).toBe('hidden');
    expect(document.body.style.overflow).toBe('hidden');
    expect(root().style.overscrollBehavior).toBe('none');
    unlock();
    expect(root().style.overflow).toBe('');
    expect(document.body.style.overflow).toBe('');
    expect(root().style.overscrollBehavior).toBe('');
  });

  it('holds the lock until the last of two overlapping owners releases', () => {
    const first = lockBodyScroll();
    const second = lockBodyScroll();
    first();
    expect(document.body.style.overflow).toBe('hidden'); // the drawer is still open
    second();
    expect(document.body.style.overflow).toBe('');
  });

  it('releasing twice frees only one hold', () => {
    const first = lockBodyScroll();
    const second = lockBodyScroll();
    first();
    first(); // a double close must not release the second owner's hold
    expect(document.body.style.overflow).toBe('hidden');
    second();
    expect(document.body.style.overflow).toBe('');
  });
});
