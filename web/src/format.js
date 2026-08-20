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

// --- Times -----------------------------------------------------------------
//
// The API sends offset-aware ISO strings: `2026-09-18T21:50:00+09:00`. Handing
// those to `new Date()` and formatting would re-render them in the *viewer's*
// timezone, so a Tokyo arrival would read as a different hour depending on who
// is looking. Every time in this app is a wall-clock time at the place it
// happens, so the components are read straight out of the string instead.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const ZONES = { '+08:00': 'SGT', '+09:00': 'JST' };

const ISO = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2})?(?:\.\d+)?([+-]\d{2}:\d{2}|Z)?$/;

export function parts(iso) {
  const match = ISO.exec(String(iso ?? ''));
  if (!match) return null;
  const [, year, month, day, hour, minute, offset] = match;
  return {
    year: Number(year),
    month: Number(month),
    day: Number(day),
    hour,
    minute,
    zone: ZONES[offset] ?? '',
  };
}

/** `21:50` */
export function timeOf(iso) {
  const p = parts(iso);
  return p ? `${p.hour}:${p.minute}` : '—';
}

/** `18 Sep 21:50` */
export function stamp(iso) {
  const p = parts(iso);
  return p ? `${p.day} ${MONTHS[p.month - 1]} ${p.hour}:${p.minute}` : '—';
}

/** `18 Sep 21:50 JST` — used where two zones appear side by side. */
export function stampZoned(iso) {
  const p = parts(iso);
  if (!p) return '—';
  return `${p.day} ${MONTHS[p.month - 1]} ${p.hour}:${p.minute}${p.zone ? ` ${p.zone}` : ''}`;
}

/** `18 Sep` */
export function dayOf(iso) {
  const p = parts(iso);
  return p ? `${p.day} ${MONTHS[p.month - 1]}` : '—';
}

/** `4h 45m`, `46h` */
export function duration(totalHours) {
  const whole = Math.floor(Math.abs(totalHours));
  const minutes = Math.round((Math.abs(totalHours) - whole) * 60);
  if (!minutes) return `${whole}h`;
  return `${whole}h ${String(minutes).padStart(2, '0')}m`;
}

/** `1h 45m` from a minute count, for dependency buffers. */
export function minutesLabel(totalMinutes) {
  const value = Math.abs(Math.round(totalMinutes));
  if (value < 60) return `${value} min`;
  const hours = Math.floor(value / 60);
  const rest = value % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

export function clockTime(totalSeconds) {
  const safe = Math.max(0, totalSeconds);
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}
