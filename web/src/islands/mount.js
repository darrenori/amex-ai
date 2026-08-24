// Bridge between the vanilla shell and the React "islands".
//
// The app stays vanilla; only a few visual components (the ReactFlow dependency
// graph, the bklit-style charts) are React. Each is mounted into a plain <div>
// the vanilla views create, and torn down before that <div> is replaced.
//
// The recovery console rewrites `body.innerHTML` on every stage change, which
// detaches any container a root was mounted into. React must be told, or it
// warns and leaks. `unmountAll()` is called at the top of each repaint; mounts
// are keyed by their container node so a stale entry is dropped when its node is.

import { createElement } from 'react';
import { createRoot } from 'react-dom/client';

/** container node -> React root */
const roots = new Map();

/**
 * Render `Component` with `props` into `container`. Reuses the root if the same
 * node is mounted again. Returns an unmount function for one-off callers.
 */
export function mountIsland(container, Component, props = {}) {
  if (!container) return () => {};
  let root = roots.get(container);
  if (!root) {
    root = createRoot(container);
    roots.set(container, root);
  }
  root.render(createElement(Component, props));
  return () => unmountIsland(container);
}

export function unmountIsland(container) {
  const root = roots.get(container);
  if (!root) return;
  roots.delete(container);
  // Defer so we never unmount synchronously inside a React render pass.
  queueMicrotask(() => root.unmount());
}

/** Tear down every mounted root (optionally only those inside `withinNode`). */
export function unmountAll(withinNode) {
  for (const [container, root] of [...roots]) {
    if (withinNode && !(withinNode === container || withinNode.contains(container))) continue;
    roots.delete(container);
    queueMicrotask(() => root.unmount());
  }
}
