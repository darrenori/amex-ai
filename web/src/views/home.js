// The marketing landing page shown before login. The markup lives in index.html;
// this wires the "Log in / Get started" buttons and the Motion.dev entrance.

import { animate, stagger } from 'motion';

export function initHome(view, { onEnter, reducedMotion }) {
  view.querySelectorAll('[data-enter-app]').forEach((btn) =>
    btn.addEventListener('click', onEnter),
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
