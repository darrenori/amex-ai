// Section 4 — the rebooking is one decision, not the whole answer. Every step that
// follows from the selected plan, in order.

import { escapeHtml } from '../format.js';
import { icons } from '../icons.js';

export function journeyMarkup(plan) {
  if (!plan) {
    return '<p class="muted" style="font-size:var(--text-sm)">Select a recovery path to see the journey it produces.</p>';
  }

  const steps = plan.journey
    .map((step) => `
      <div class="journey-step">
        <div class="journey-line" aria-hidden="true"></div>
        <div class="journey-node ${escapeHtml(step.tone)}" aria-hidden="true">${icons[step.icon] ?? icons.plane}</div>
        <div>
          <p class="journey-label">${escapeHtml(step.label)}</p>
          <p class="journey-title">${escapeHtml(step.title)}</p>
          <p class="journey-detail">${escapeHtml(step.detail)}</p>
          ${step.included ? `<p class="journey-included">✓ ${escapeHtml(step.included)}</p>` : ''}
        </div>
      </div>`)
    .join('');

  return `
    <p class="journey-for">Journey for <strong>${escapeHtml(plan.name)}</strong> · ${escapeHtml(plan.carrier)}</p>
    <div class="journey">${steps}</div>
  `;
}
