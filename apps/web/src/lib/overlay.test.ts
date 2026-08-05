// The outside-click judgement. The interesting case is the one that shipped as a bug: the
// clicked row is re-rendered away between its own handler and the document-level one, so
// containment checked at handler time reads a detached node and reports "outside" for the
// very element that was clicked.

import { describe, expect, it } from 'vitest';

import { clickedOutside } from './overlay';

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
