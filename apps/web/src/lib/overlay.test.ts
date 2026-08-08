// The outside-click judgement. The interesting case is the one that shipped as a bug: the
// clicked row is re-rendered away between its own handler and the document-level one, so
// containment checked at handler time reads a detached node and reports "outside" for the
// very element that was clicked.

import { describe, expect, it } from 'vitest';

import { clickedOutside, lockBodyScroll, trapFocus } from './overlay';

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

// The trap keys visibility on offsetParent, which jsdom always reports as null because it
// computes no layout - so visibility is modelled explicitly here: a "visible" control gets
// a stubbed offsetParent, a hidden one keeps jsdom's null, which is exactly the signal the
// trap's filter reads.
describe('trapFocus', () => {
  function control(visible = true): HTMLButtonElement {
    const el = document.createElement('button');
    if (visible) {
      Object.defineProperty(el, 'offsetParent', { get: () => document.body });
    }
    return el;
  }

  function mount(...nodes: HTMLElement[]): HTMLElement {
    const container = document.createElement('div');
    container.append(...nodes);
    document.body.appendChild(container);
    return container;
  }

  function tab(container: HTMLElement, shiftKey = false): KeyboardEvent {
    const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey, cancelable: true });
    trapFocus(container, event);
    return event;
  }

  it('wraps Tab from the last control back to the first', () => {
    const first = control();
    const last = control();
    const container = mount(first, last);
    last.focus();
    const event = tab(container);
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(first);
    container.remove();
  });

  it('wraps Shift+Tab from the first control to the last', () => {
    const first = control();
    const last = control();
    const container = mount(first, last);
    first.focus();
    const event = tab(container, true);
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(last);
    container.remove();
  });

  it('leaves a mid-list Tab to the browser', () => {
    const first = control();
    const middle = control();
    const last = control();
    const container = mount(first, middle, last);
    middle.focus();
    const event = tab(container);
    expect(event.defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(middle);
    container.remove();
  });

  it('skips hidden controls when picking the endpoints', () => {
    const hiddenFirst = control(false);
    const first = control();
    const last = control();
    const hiddenLast = control(false);
    const container = mount(hiddenFirst, first, last, hiddenLast);
    last.focus();
    const event = tab(container);
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(first); // not the hidden one before it
    container.remove();
  });

  it('is a no-op in a container with nothing focusable', () => {
    const container = mount();
    const event = tab(container);
    expect(event.defaultPrevented).toBe(false);
    container.remove();
  });

  it('ignores every key but Tab', () => {
    const only = control();
    const container = mount(only);
    only.focus();
    const event = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true });
    trapFocus(container, event);
    expect(event.defaultPrevented).toBe(false);
    container.remove();
  });
});
