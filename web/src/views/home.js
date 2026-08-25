// The marketing landing page shown before login. The markup lives in index.html;
// this wires the entry buttons and the Motion.dev entrance.

import { animate, stagger } from 'motion';

export function initHome(view, { onEnter, reducedMotion }) {
  view.querySelectorAll('[data-enter-app]').forEach((btn) =>
    // Pass the button, so the handler can show progress on the control that
    // was actually pressed rather than guessing which one it was.
    btn.addEventListener('click', (event) => onEnter(event.currentTarget)),
  );

  if (reducedMotion) return () => {};

  const items = view.querySelectorAll('[data-animate]');
  const controls = animate(
    items,
    { opacity: [0, 1], transform: ['translateY(14px)', 'translateY(0)'] },
    { delay: stagger(0.07), duration: 0.55, ease: [0.22, 1, 0.36, 1] },
  );

  return () => controls.stop?.();
}
