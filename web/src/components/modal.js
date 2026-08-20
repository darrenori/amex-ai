// Shared dialog: focus trap, Escape to dismiss, focus restored to the trigger.

const overlay = document.getElementById('modalOverlay');
const panel = document.getElementById('modalPanel');
const content = document.getElementById('modalContent');
const closeButton = document.getElementById('modalClose');

let returnFocus = null;

const focusable = () =>
  [...panel.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')]
    .filter((el) => !el.disabled && el.offsetParent !== null);

export function openModal(trigger, html) {
  returnFocus = trigger ?? null;
  content.innerHTML = html;
  overlay.hidden = false;
  document.body.style.overflow = 'hidden';
  window.setTimeout(() => panel.focus(), 20);
  return content;
}

export function closeModal(restoreFocus = true) {
  if (overlay.hidden) return;
  overlay.hidden = true;
  content.innerHTML = '';
  document.body.style.overflow = '';
  if (restoreFocus && returnFocus && document.contains(returnFocus)) returnFocus.focus();
  returnFocus = null;
}

export function isModalOpen() {
  return !overlay.hidden;
}

closeButton.addEventListener('click', () => closeModal());
overlay.addEventListener('click', (event) => {
  if (event.target === overlay) closeModal();
});

document.addEventListener('keydown', (event) => {
  if (overlay.hidden) return;

  if (event.key === 'Escape') {
    closeModal();
    return;
  }

  if (event.key !== 'Tab') return;

  const items = focusable();
  if (!items.length) return;

  const first = items[0];
  const last = items[items.length - 1];

  if (event.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

/** Every dialog in the app is delegated: any element with data-close-modal dismisses it. */
content.addEventListener('click', (event) => {
  if (event.target.closest('[data-close-modal]')) closeModal();
});
