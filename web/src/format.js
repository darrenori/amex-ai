// Formatting helpers. Every figure the user reads goes through here so currency,
// sign convention and thousands separators stay identical across views.

export const CURRENCY = 'SGD';

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[char]);
}

const grouped = (value) => Math.abs(Math.round(value)).toLocaleString('en-SG');

/** `SGD 4,980` — magnitude only, sign shown as a leading minus. */
export function money(value, currency = CURRENCY) {
  return `${value < 0 ? '-' : ''}${currency} ${grouped(value)}`;
}

/** `+SGD 180` / `-SGD 400` — always signed, for deltas against the confirmed trip. */
export function signedMoney(value, currency = CURRENCY) {
  return `${value >= 0 ? '+' : '-'}${currency} ${grouped(value)}`;
}

/** Net whole-trip impact, phrased the way a Card Member would read it. */
export function netPhrase(value, currency = CURRENCY) {
  if (value === 0) return 'Break-even';
  return value < 0
    ? `${money(Math.abs(value), currency)} net saved`
    : `${money(value, currency)} net cost`;
}

export function points(value) {
  return Number(value).toLocaleString('en-SG');
}

export function hours(value) {
  return `${value} hr${value === 1 ? '' : 's'}`;
}

export function clockTime(totalSeconds) {
  const safe = Math.max(0, totalSeconds);
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}
