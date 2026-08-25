// The walkthrough.
//
// TripShield does something a traveller has not seen before: it treats a trip
// as a graph and prices a cancellation against the whole of it. Someone landing
// on stage 1 has no reason to expect that, so this explains the shape of the
// solution before they start, in one line per stage.
//
// It opens itself once, remembers that it has, and stays available from the app
// bar afterwards. Nothing here blocks the demo.

import { closeModal, openModal } from '../components/modal.js';
import { icons } from '../icons.js';

const SEEN_KEY = 'tripshield.tour.v1';

const STEPS = [
  {
    icon: icons.radar,
    title: 'We watch the flights',
    body: 'Every booking sits on one Card, so a cancellation is found before you hear about it.',
  },
  {
    icon: icons.graph,
    title: 'The trip is a graph',
    body: 'A hard link cancels what it feeds. A soft one only frays it. Drag a booking to trace its chain.',
  },
  {
    icon: icons.scale,
    title: 'Whole plans, not parts',
    body: 'Each card is one complete recovery, priced across the journey rather than the fare.',
  },
  {
    icon: icons.grip,
    title: 'Yours to adjust',
    body: 'Swap any option by hand. The server revalidates every change.',
  },
  {
    icon: icons.shield,
    title: 'Nothing charged until you say',
    body: 'Steps commit one at a time, in order, and every one can be backed out.',
  },
];

function stepMarkup(step, index) {
  return `
    <li class="tour-step">
      <span class="tour-step-icon" aria-hidden="true">${step.icon}</span>
      <div>
        <p class="tour-step-title"><span class="tour-step-num">${index + 1}</span>${step.title}</p>
        <p class="tour-step-body">${step.body}</p>
      </div>
    </li>`;
}

/** Open the walkthrough. `trigger` gets focus back when it closes. */
export function openTutorial(trigger = null) {
  const content = openModal(trigger, `
    <div class="tour">
      <p class="eyebrow">How it works</p>
      <h3 id="modalTitle" class="tour-title">One cancellation, priced against the whole trip.</h3>
      <ol class="tour-steps">${STEPS.map(stepMarkup).join('')}</ol>
      <p class="tour-foot">Every figure here is synthetic. Nothing is really booked or charged.</p>
      <div class="modal-actions">
        <button class="btn btn-primary" type="button" id="tourDone">Start</button>
      </div>
    </div>
  `);
  content.querySelector('#tourDone').addEventListener('click', () => closeModal());
  try {
    window.localStorage.setItem(SEEN_KEY, '1');
  } catch {
    // Private browsing can refuse storage. Showing the tour twice is harmless.
  }
}

/** Open it once per browser, the first time someone reaches the app. */
export function maybeOpenTutorial() {
  let seen = false;
  try {
    seen = window.localStorage.getItem(SEEN_KEY) === '1';
  } catch {
    seen = false;
  }
  if (!seen) window.setTimeout(() => openTutorial(null), 600);
}
