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

const SERVICE_DEFAULTS = {
  flight: {
    label: 'Open Amex Travel flights',
    url: 'https://www.americanexpress.com/en-sg/travel/flights/',
  },
  lodging: {
    label: 'Open Amex Travel hotels',
    url: 'https://www.americanexpress.com/en-sg/travel/hotels/',
  },
  dining: {
    label: 'Open Love Dining restaurants',
    url: 'https://www.americanexpress.com/sg/benefits/love-dining/love-restaurants.html',
  },
  ground: {
    label: 'Open Amex Travel transport',
    url: 'https://www.americanexpress.com/en-sg/travel/cars/',
  },
};

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

/** Return the best approved destination for the service represented by an option. */
export function optionServiceLink(option) {
  if (!option) return null;

  if (option.kind === 'lodging') {
    const partnerUrl = isAmexUrl(option.amex_partner?.official_url ?? option.amex_partner?.url);
    if (partnerUrl) {
      return {
        label: `Open ${option.supplier ?? 'hotel'} details`,
        url: partnerUrl.href,
      };
    }
  }

  const keyword = {
    lodging: /hotel/i,
    dining: /dining|restaurant/i,
    ground: /car|transport/i,
  }[option.kind];
  if (keyword) {
    const matching = (option.links ?? []).find((link) => keyword.test(String(link?.label ?? '')));
    const matchingUrl = isAmexUrl(matching?.url);
    if (matchingUrl) return { label: matching.label, url: matchingUrl.href };
  }

  const fallback = SERVICE_DEFAULTS[option.kind];
  const fallbackUrl = isAmexUrl(fallback?.url);
  return fallbackUrl ? { ...fallback, url: fallbackUrl.href } : null;
}

/** Render an option title as an external service link when one is available. */
export function optionServiceLinkMarkup(option, { className = 'option-service-link' } = {}) {
  const service = optionServiceLink(option);
  if (!service) return escapeHtml(option?.title ?? '');
  return `
    <a class="${escapeHtml(className)}" href="${escapeHtml(service.url)}"
       target="_blank" rel="noopener noreferrer"
       aria-label="${escapeHtml(`${option.title}, ${service.label} (opens in a new tab)`)}">
      <span>${escapeHtml(option.title)}</span><span aria-hidden="true">↗</span>
    </a>`;
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
