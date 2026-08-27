// The marketing landing page shown before login. The markup lives in index.html;
// this wires the entry buttons and the Motion.dev entrance.

import { animate, inView, scroll, stagger } from 'motion';

export function initHome(view, { onEnter, reducedMotion }) {
  view.querySelectorAll('[data-enter-app]').forEach((btn) =>
    // Pass the button, so the handler can show progress on the control that
    // was actually pressed rather than guessing which one it was.
    btn.addEventListener('click', (event) => onEnter(event.currentTarget)),
  );

  if (reducedMotion) return () => {};

  const items = view.querySelectorAll('[data-animate]');
  const entrance = animate(
    items,
    { opacity: [0, 1], y: [18, 0] },
    { delay: stagger(0.08), duration: 0.7, ease: [0.22, 1, 0.36, 1] },
  );

  const cleanups = [];
  const visual = view.querySelector('[data-parallax-root]');
  const image = view.querySelector('[data-parallax-image]');

  if (visual && image) {
    const parallax = animate(
      image,
      { y: ['-3%', '3%'], scale: [1.06, 1.02] },
      { ease: 'linear' },
    );
    cleanups.push(scroll(parallax, {
      target: visual,
      offset: ['start end', 'end start'],
    }));
  }

  const revealItems = view.querySelectorAll('[data-reveal]');
  revealItems.forEach((item) => { item.style.opacity = '0'; });
  cleanups.push(inView(revealItems, (item) => {
    animate(item, { opacity: [0, 1], y: [22, 0] }, {
      duration: 0.65,
      ease: [0.22, 1, 0.36, 1],
    });
  }, { amount: 0.2 }));

  return () => {
    entrance.stop?.();
    cleanups.forEach((cleanup) => cleanup?.());
  };
}
