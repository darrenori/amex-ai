// Outbound links to American Express properties, rendered under a recovery step.
//
// The allowlist is the point. These URLs arrive from the API as data, and data
// is not trusted just because it came from our own backend — a link is the one
// thing on this screen that can take a Card Member somewhere else entirely, so
// anything that is not `https://` on americanexpress.com is dropped silently
// rather than rendered. Enforcing it here, at the render boundary, means it
// holds no matter which connector or agent produced the link.
//
// `rel="noopener noreferrer"` is not optional on a `target="_blank"` link: it
// stops the opened page reaching back through `window.opener`.

import { escapeHtml } from '../format.js';

const ALLOWED_HOST = 'americanexpress.com';

function isAmexUrl(raw) {
  let url;
  try {
    url = new URL(String(raw ?? ''));
  } catch {
    return null;
  }
  if (url.protocol !== 'https:') return null;
  const host = url.hostname.toLowerCase();
  if (host !== ALLOWED_HOST && !host.endsWith(`.${ALLOWED_HOST}`)) return null;
  return url;
}

export function partnerLinksMarkup(links = [], { label = '' } = {}) {
  const items = links
    .map((link) => {
      const url = isAmexUrl(link?.url);
      if (!url) return '';
      return `
        <a class="journey-action" href="${escapeHtml(url.href)}" target="_blank" rel="noopener noreferrer">
          <span>${escapeHtml(link.label)}</span>
          <span aria-hidden="true">↗</span>
          <span class="sr-only"> (opens in a new tab)</span>
        </a>`;
    })
    .filter(Boolean)
    .join('');

  if (!items) return '';
  return `
    <div class="journey-actions">
      ${label ? `<span class="journey-actions-label">${escapeHtml(label)}</span>` : ''}
      ${items}
    </div>`;
}

/** Dedupe by href across a plan's options, so one card does not repeat a link. */
export function collectLinks(options = []) {
  const seen = new Map();
  options.forEach((option) => {
    (option.links ?? []).forEach((link) => {
      const url = isAmexUrl(link?.url);
      if (url && !seen.has(url.href)) seen.set(url.href, { label: link.label, url: url.href });
    });
  });
  return [...seen.values()];
}
