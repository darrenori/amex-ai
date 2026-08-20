// Section 4 — the rebooking is one decision, not the whole answer. Every step that
// follows from the selected plan, in order.

import { escapeHtml } from '../format.js';
import { icons } from '../icons.js';

function partnerLinksMarkup(links = []) {
  const items = links
    .map((link) => {
      try {
        const url = new URL(String(link.url ?? ''));
        const host = url.hostname.toLowerCase();
        const isAmex = host === 'americanexpress.com' || host.endsWith('.americanexpress.com');
        if (url.protocol !== 'https:' || !isAmex) return '';

        return `
          <a class="journey-action" href="${escapeHtml(url.href)}" target="_blank" rel="noopener noreferrer">
            <span>${escapeHtml(link.label)}</span>
            <span aria-hidden="true">↗</span>
            <span class="sr-only"> (opens in a new tab)</span>
          </a>`;
      } catch {
        return '';
      }
    })
    .filter(Boolean)
    .join('');

  return items ? `<div class="journey-actions">${items}</div>` : '';
}

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
          ${partnerLinksMarkup(step.links)}
        </div>
      </div>`)
    .join('');

  return `
    <p class="journey-for">Journey for <strong>${escapeHtml(plan.name)}</strong> · ${escapeHtml(plan.carrier)}</p>
    <div class="journey">${steps}</div>
  `;
}
