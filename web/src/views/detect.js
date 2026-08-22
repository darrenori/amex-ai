// Stage 1 — detection.
//
// Member-facing detection. Technical connector diagnostics remain available
// from the backend endpoints, but this screen only explains what happened and
// what TripShield found in ordinary travel language.

import { escapeHtml, stampZoned } from '../format.js';
import { icons } from '../icons.js';

const modeTone = (mode) => ({ live: 'good', sandbox: 'warn', fixture: 'neutral' })[mode] ?? 'neutral';
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function travelDate(value) {
  const [year, month, day] = String(value ?? '').split('-').map(Number);
  if (!year || !month || !day || !MONTHS[month - 1]) return String(value ?? 'Upcoming');
  return `${day} ${MONTHS[month - 1]} ${year}`;
}

function friendlyStatus(status) {
  if (status === 'Canceled' || status === 'CanceledUncertain') return 'Cancelled';
  return String(status ?? 'Status unavailable');
}

function updateLabel(mode) {
  if (mode === 'live') return 'Latest status';
  if (mode === 'sandbox') return 'Test status';
  return 'Demo status';
}

function updateNote(mode) {
  if (mode === 'live') {
    return 'TripShield checked the latest available travel update for every upcoming flight.';
  }
  if (mode === 'sandbox') {
    return 'This demonstration is using safe test travel updates. No booking was changed.';
  }
  return 'This demonstration is using recorded sample travel updates. No booking was changed.';
}

function checkRow(check) {
  const tone = check.disruptive ? 'critical' : 'good';
  const status = friendlyStatus(check.status);
  const label = check.disruptive ? status : `${status} · on schedule`;
  return `
    <li class="sweep-row${check.disruptive ? ' is-hit' : ''}">
      <span class="icon-badge ${tone}" aria-hidden="true">${check.disruptive ? icons.alert : icons.check}</span>
      <span class="sweep-main">
        <span class="sweep-flight">${escapeHtml(check.flight_number)}</span>
        <span class="sweep-meta">${escapeHtml(travelDate(check.date))} · checked automatically</span>
      </span>
      <span class="chip chip-${tone}"><span class="dot"></span>${escapeHtml(label)}</span>
    </li>`;
}

export function detectMarkup(detection) {
  const mode = detection.connector.mode ?? 'fixture';
  const disruption = detection.disruptions[0] ?? null;
  const hit = detection.checks.find((c) => c.disruptive) ?? null;

  return `
    <div class="section-head">
      <h3><span class="section-index">1.</span> Detection</h3>
      <p>
        TripShield automatically checks upcoming flights for important travel changes. It found this
        disruption early, so the rest of the trip can be reviewed before you need to contact each
        travel provider yourself.
      </p>
    </div>

    <div class="card">
      <div class="card-head">
        <div>
          <p class="eyebrow">Travel monitoring</p>
          <h4 class="stage-title">Upcoming flight checks</h4>
        </div>
        <span class="chip chip-${modeTone(mode)}">
          <span class="dot"></span>${escapeHtml(updateLabel(mode))}
        </span>
      </div>

      <p class="muted stage-note">
        ${escapeHtml(updateNote(mode))}
      </p>

      <ul class="sweep-list">${detection.checks.map(checkRow).join('')}</ul>

      ${disruption ? `
        <div class="alert alert-tight" role="status">
          <span class="icon-badge critical" aria-hidden="true">${icons.alert}</span>
          <div>
            <strong>${escapeHtml(hit?.flight_number ?? 'Your flight')} · Flight cancelled</strong>
            <p>${escapeHtml(disruption.reason)} Detected ${escapeHtml(stampZoned(disruption.detected_at))},
               without the member reporting anything.</p>
          </div>
        </div>` : `
        <p class="muted stage-note">No disruption on any flight for this trip.</p>`}

      ${hit ? `
        <p class="muted stage-note">
          We will now check how this change affects your hotel, activities, meals and transfers.
        </p>` : ''}
    </div>`;
}
