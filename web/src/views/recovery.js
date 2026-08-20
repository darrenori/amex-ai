// The disruption view. This is where the two source prototypes meet: the
// AMEX Travel recovery flow (alert, countdown, review-before-anything-changes
// dialog, confirmation and undo) wrapped around TripShield's whole-trip
// arithmetic (snapshot, net financial impact, personalized scoring, journey).

import { api } from '../api.js';
import { clockTime, escapeHtml, money, netPhrase, signedMoney } from '../format.js';
import { icons, kindIcon, statusTone } from '../icons.js';
import { openModal, closeModal } from '../components/modal.js';
import { intelligenceMarkup, profileToggleMarkup } from './intelligence.js';
import { journeyMarkup } from './journey.js';

// Fixed diverging domain so the three net-impact bars are read against each other.
const NET_DOMAIN = 600;

// Minutes at which the hotel deadline is announced to assistive technology.
const MILESTONES = [30, 15, 10, 5, 2, 1, 0];

function snapshotCard(component, currency) {
  const tone = statusTone[component.status] ?? 'neutral';
  const cancelled = component.status === 'cancelled';
  return `
    <article class="card card-tight snap-card is-${escapeHtml(component.status)}">
      <div class="snap-top">
        <span style="display:flex;align-items:center;gap:12px">
          <span class="icon-badge ${tone}" aria-hidden="true">${kindIcon[component.kind] ?? icons.plane}</span>
          <span class="snap-label">${escapeHtml(component.label)}</span>
        </span>
        <span class="chip chip-${tone}"><span class="dot"></span>${escapeHtml(component.status_label)}</span>
      </div>
      <p class="snap-title">${cancelled ? `<del>${escapeHtml(component.title)}</del>` : escapeHtml(component.title)}</p>
      <p class="snap-detail">${escapeHtml(component.note || component.detail)}</p>
      <p class="snap-amount">Charged to this Card <strong>${money(component.amount, currency)}</strong></p>
    </article>`;
}

function pathCard(plan, recommended, currency) {
  const net = plan.net_impact;
  const width = Math.min(Math.abs(net) / NET_DOMAIN, 1) * 50;
  const fill = net >= 0
    ? `<div class="diverge-fill cost" style="width:${width.toFixed(1)}%"></div>`
    : `<div class="diverge-fill save" style="width:${width.toFixed(1)}%"></div>`;

  return `
    <article class="card path-card${recommended ? ' is-recommended' : ''}" data-plan-card="${escapeHtml(plan.id)}">
      <div class="path-head">
        <span class="icon-badge" aria-hidden="true">${icons.plane}</span>
        <div>
          <p class="path-name">${escapeHtml(plan.name)}</p>
          <span class="path-tag">${escapeHtml(plan.tag)}</span>
        </div>
        ${recommended ? '<span class="badge-rec" style="margin-left:auto">Recommended</span>' : ''}
      </div>

      <div class="path-line">
        <span class="lbl">${escapeHtml(plan.carrier)}</span>
        <span class="chip chip-${plan.stops_status === 'good' ? 'good' : 'warn'}"><span class="dot"></span>${escapeHtml(plan.stops)}</span>
      </div>
      <p class="path-sched">${escapeHtml(plan.schedule)}</p>

      <hr class="divider">

      <div class="path-line"><span class="lbl">Fare vs. original</span><span class="val ${plan.fare_delta <= 0 ? 'pos' : 'neg'}">${signedMoney(plan.fare_delta, currency)}</span></div>
      <div class="path-line"><span class="lbl">Hotel impact</span><span class="val">${plan.hotel_impact ? signedMoney(plan.hotel_impact, currency) : `${currency} 0`}</span></div>
      <div class="path-line"><span class="lbl">Car rental impact</span><span class="val">${plan.car_impact ? signedMoney(plan.car_impact, currency) : `${currency} 0`}</span></div>
      <div class="path-line"><span class="lbl">Time lost</span><span class="val">${plan.hours_lost} hr</span></div>

      <hr class="divider">

      <div>
        <div class="diverge-label"><span>Net saving</span><span>Net cost</span></div>
        <div class="diverge-track" role="img" aria-label="Net whole-trip impact ${netPhrase(net, currency)}">
          <div class="diverge-mid" aria-hidden="true"></div>
          ${fill}
        </div>
        <p class="diverge-figure ${net <= 0 ? 'pos' : 'neg'}">${netPhrase(net, currency)}</p>
      </div>

      <hr class="divider">

      <p class="subrow ${escapeHtml(plan.hotel_note.status)}"><span class="dot" aria-hidden="true"></span><span>${escapeHtml(plan.hotel_note.text)}</span></p>
      <p class="subrow ${escapeHtml(plan.car_note.status)}"><span class="dot" aria-hidden="true"></span><span>${escapeHtml(plan.car_note.text)}</span></p>
    </article>`;
}

function pickerMarkup(recovery, selectedId, currency) {
  return recovery.scored
    .map(({ plan, net_impact: net }) => {
      const selected = plan.id === selectedId;
      const recommended = plan.id === recovery.recommended_plan_id;
      return `
        <button type="button" class="plan-option${selected ? ' is-selected' : ''}" data-plan="${escapeHtml(plan.id)}"
                aria-pressed="${selected}"
                aria-label="${escapeHtml(plan.name)}${recommended ? ', recommended' : ''}: ${signedMoney(plan.fare_delta, currency)} fare, ${plan.hours_lost} hours lost, ${netPhrase(net, currency)}">
          <span class="plan-radio" aria-hidden="true"></span>
          <span class="plan-main">
            <span class="plan-name-row">
              <span class="plan-name">${escapeHtml(plan.name)}</span>
              ${recommended ? '<span class="badge-rec">Recommended</span>' : ''}
            </span>
            <span class="plan-stat">${signedMoney(plan.fare_delta, currency)} fare · ${plan.hours_lost}h lost · ${netPhrase(net, currency)}</span>
          </span>
        </button>`;
    })
    .join('');
}

export function renderRecovery(container, { payload, profiles, onBack, announce }) {
  const { disruption, trip, currency } = payload;

  const state = {
    profileId: payload.recovery.profile.id,
    recovery: payload.recovery,
    selectedPlanId: payload.recovery.recommended_plan_id,
    confirmed: false,
    heldPlanId: null,
    secondsLeft: disruption.hotel_deadline_seconds,
    lastAnnouncedMinute: null,
    timer: null,
  };

  const planById = (id) => state.recovery.scored.find((row) => row.plan.id === id)?.plan ?? null;

  container.innerHTML = `
    <div class="page-head">
      <button class="btn btn-quiet" id="backToAccount" type="button">${icons.arrowLeft} Back to account</button>
      <p class="eyebrow" style="margin-top:24px">TripShield recovery</p>
      <h2>Your flight was cancelled. Your trip can still move forward.</h2>
      <p>Every booking on this trip was charged to the same Card, so the disruption is priced against all of them — not just the flight.</p>
    </div>

    <div class="alert" role="status">
      <span class="icon-badge critical" aria-hidden="true">${icons.alert}</span>
      <div>
        <strong>${escapeHtml(disruption.headline)}</strong>
        <p>${escapeHtml(disruption.detail)} Arrival needed before ${escapeHtml(disruption.arrive_by)}.</p>
      </div>
    </div>

    <section aria-labelledby="snapshotHeading">
      <div class="section-head">
        <h3 id="snapshotHeading"><span class="section-index">1.</span> Trip snapshot</h3>
        <p>A disruption to one leg is visible against every other booking on the trip, with the amount already charged to the Card.</p>
      </div>
      <div class="snapshot-grid">
        ${trip.components.filter((c) => c.kind !== 'fees').map((c) => snapshotCard(c, currency)).join('')}
      </div>
    </section>

    <section aria-labelledby="valueHeading">
      <div class="section-head">
        <h3 id="valueHeading"><span class="section-index">2.</span> What is at stake</h3>
        <p>The confirmed trip total and the window before the first non-refundable night is lost.</p>
      </div>
      <div class="deadline-grid">
        <article class="card">
          <h4 style="font-size:var(--text-sm)">Trip value at a glance</h4>
          <div class="impact-row">
            <div><span>Confirmed trip total</span><strong>${money(trip.total, currency)}</strong></div>
            <div><span>Non-refundable tonight</span><strong class="neg">${money(disruption.hotel_at_risk, currency)}</strong></div>
            <div><span>Emergency seat hold</span><strong>${disruption.emergency_hold_minutes} min</strong></div>
          </div>
          <p class="subdued" style="margin-top:16px">The seat hold runs independently of the hotel deadline.</p>
        </article>
        <article class="card deadline-card on-navy">
          <h3>Hotel refund deadline</h3>
          <p class="countdown" id="countdown" aria-label="${Math.round(state.secondsLeft / 60)} minutes remaining">${clockTime(state.secondsLeft)}</p>
          <p>Resolve within this window to protect ${money(disruption.hotel_at_risk, currency)} of tonight's stay.</p>
        </article>
      </div>
    </section>

    <section aria-labelledby="pathsHeading">
      <div class="section-head">
        <h3 id="pathsHeading"><span class="section-index">3.</span> Recovery paths, scored on more than the fare</h3>
        <p>Each option's net financial impact folds in what it saves or forfeits elsewhere on the trip, not just the price of the new flight.</p>
      </div>
      <div class="path-grid" id="pathGrid"></div>
    </section>

    <section aria-labelledby="recHeading">
      <div class="section-head">
        <h3 id="recHeading"><span class="section-index">4.</span> Personalized recommendation</h3>
        <p>The traveler never picks a profile. The system already knows their weight from real history and sends one rebooking, already decided.</p>
      </div>

      <div class="card">
        <div class="notif">
          <span class="notif-icon" aria-hidden="true">TS</span>
          <div style="flex:1;min-width:0">
            <div class="notif-top"><span class="notif-app">TripShield</span><span class="notif-time">now</span></div>
            <p class="notif-msg" id="notifMsg"></p>
          </div>
        </div>

        <div class="plan-picker" id="planPicker"></div>

        <div class="confirm-row">
          <p class="confirm-note" id="confirmNote"></p>
          <div class="confirm-actions">
            <button class="btn btn-ghost" id="holdButton" type="button">Hold seat ${disruption.emergency_hold_minutes} min</button>
            <button class="btn btn-primary" id="confirmButton" type="button">Confirm plan change</button>
          </div>
        </div>

        <p class="hold-note" id="holdNote" hidden></p>

        <div class="plan-success" id="planSuccess" hidden>
          <div class="success-copy">
            <span class="success-mark" aria-hidden="true">✓</span>
            <div>
              <strong id="successTitle"></strong>
              <p id="successMessage"></p>
            </div>
          </div>
          <button class="btn btn-quiet" id="undoButton" type="button">Undo change</button>
        </div>

        <p class="subdued" style="margin-top:16px">This is the entire traveler-facing experience — one clear recommendation up front, every alternative one tap away. The final choice is always theirs.</p>

        <p class="inspect-divider">Behind the scenes — how the model got there</p>
        <p class="muted" style="font-size:var(--text-sm);margin-bottom:16px">
          The control below is not traveler-facing. It is an inspection view, so you can watch the same
          disruption resolve differently for a different real history. A given Card Member only ever has one active.
        </p>

        <div class="toggle-group" id="profileToggles" role="group" aria-label="Inferred traveler profile"></div>
        <div id="intelligence"></div>
      </div>
    </section>

    <section aria-labelledby="journeyHeading">
      <div class="section-head">
        <h3 id="journeyHeading"><span class="section-index">5.</span> Your recovery journey</h3>
        <p>Where to wait, how to get there, what to eat, and what happens to the rest of the trip — all of it follows from whichever path is selected above.</p>
      </div>
      <div class="card" id="journeyCard"></div>
      <p class="rank-note">Every merchant step is ranked by proximity, availability and fit — never by promotional placement</p>
    </section>
  `;

  const el = {
    pathGrid: container.querySelector('#pathGrid'),
    notifMsg: container.querySelector('#notifMsg'),
    picker: container.querySelector('#planPicker'),
    confirmNote: container.querySelector('#confirmNote'),
    confirmButton: container.querySelector('#confirmButton'),
    holdButton: container.querySelector('#holdButton'),
    holdNote: container.querySelector('#holdNote'),
    success: container.querySelector('#planSuccess'),
    successTitle: container.querySelector('#successTitle'),
    successMessage: container.querySelector('#successMessage'),
    undoButton: container.querySelector('#undoButton'),
    toggles: container.querySelector('#profileToggles'),
    intelligence: container.querySelector('#intelligence'),
    journey: container.querySelector('#journeyCard'),
    countdown: container.querySelector('#countdown'),
  };

  // ---------------- Rendering ----------------

  function paint() {
    const { recovery } = state;

    el.pathGrid.innerHTML = recovery.scored
      .map(({ plan }) => pathCard(plan, plan.id === recovery.recommended_plan_id, currency))
      .join('');

    el.notifMsg.innerHTML = escapeHtml(recovery.notification).replace(
      escapeHtml(planById(recovery.recommended_plan_id).name),
      `<strong>${escapeHtml(planById(recovery.recommended_plan_id).name)}</strong>`,
    );

    el.picker.innerHTML = pickerMarkup(recovery, state.selectedPlanId, currency);
    el.toggles.innerHTML = profileToggleMarkup(profiles, state.profileId);
    el.intelligence.innerHTML = intelligenceMarkup(recovery, currency);

    const selected = planById(state.selectedPlanId);
    el.journey.innerHTML = journeyMarkup(selected);

    if (!state.confirmed && selected) {
      const isRecommended = state.selectedPlanId === recovery.recommended_plan_id;
      el.confirmNote.textContent =
        `Selected: ${selected.name}${isRecommended ? ' (the recommendation)' : ''} · revised total ${money(selected.new_total, currency)}.`;
    }
  }

  function showSuccess(result) {
    state.confirmed = true;
    el.success.hidden = false;
    el.successTitle.textContent = result.title;
    el.successMessage.textContent = result.message;
    el.confirmButton.disabled = true;
    el.holdButton.disabled = true;
    el.confirmNote.textContent = result.followed_recommendation
      ? 'You confirmed the recommended path.'
      : 'You confirmed a path other than the recommendation. That is always available.';
    announce(`${result.title} ${result.message}`);
    window.setTimeout(() => el.undoButton.focus(), 40);
  }

  function resetSelection() {
    state.confirmed = false;
    state.heldPlanId = null;
    el.success.hidden = true;
    el.holdNote.hidden = true;
    el.confirmButton.disabled = false;
    el.holdButton.disabled = false;
    state.selectedPlanId = state.recovery.recommended_plan_id;
    paint();
  }

  // ---------------- Events ----------------

  container.querySelector('#backToAccount').addEventListener('click', onBack);

  el.picker.addEventListener('click', (event) => {
    const option = event.target.closest('[data-plan]');
    if (!option || state.confirmed) return;
    state.selectedPlanId = option.dataset.plan;
    paint();
    announce(`${planById(state.selectedPlanId).name} selected.`);
  });

  el.toggles.addEventListener('click', async (event) => {
    const option = event.target.closest('[data-profile]');
    if (!option || option.dataset.profile === state.profileId) return;

    state.profileId = option.dataset.profile;
    try {
      const result = await api.recovery(state.profileId);
      state.recovery = result.recovery;
      // A fresh inference produces a fresh recommendation, so the selection resets.
      state.selectedPlanId = state.recovery.recommended_plan_id;
      state.confirmed = false;
      state.heldPlanId = null;
      el.success.hidden = true;
      el.holdNote.hidden = true;
      el.confirmButton.disabled = false;
      el.holdButton.disabled = false;
      paint();
      announce(`Profile changed to ${state.recovery.profile.name}. ${state.recovery.explanation}`);
    } catch (error) {
      announce(error.message);
    }
  });

  el.holdButton.addEventListener('click', async () => {
    try {
      const result = await api.hold(state.selectedPlanId);
      state.heldPlanId = result.plan_id;
      el.holdNote.hidden = false;
      el.holdNote.textContent = result.message;
      announce(result.message);
    } catch (error) {
      el.holdNote.hidden = false;
      el.holdNote.textContent = error.message;
    }
  });

  el.confirmButton.addEventListener('click', (event) => {
    const plan = planById(state.selectedPlanId);
    if (!plan) return;
    // Confirming a seat that was already held is the emergency path; the API
    // words the confirmation differently for it.
    const emergency = state.heldPlanId === plan.id;

    const modal = openModal(event.currentTarget, `
      <p class="eyebrow">Review before anything changes</p>
      <h2 id="modalTitle">Confirm ${escapeHtml(plan.name)}</h2>
      <p class="modal-intro">${escapeHtml(plan.objective)}. Nothing is booked until you confirm.</p>
      <ol class="approval-list">
        <li>${escapeHtml(plan.first_step)}</li>
        <li>Keep the cabin as ${escapeHtml(plan.cabin.toLowerCase())} for the complete journey.</li>
        <li>Apply the whole-trip impact of ${netPhrase(plan.net_impact, currency)}, for a revised total of ${money(plan.new_total, currency)}.</li>
        <li>${escapeHtml(plan.hotel_note.text)} · ${escapeHtml(plan.car_note.text)}.</li>
      </ol>
      <div class="modal-summary"><span>Arrival</span><strong>${escapeHtml(plan.arrival)}</strong></div>
      ${emergency ? '<p class="hold-note">A replacement seat in the same cabin is held while you review.</p>' : ''}
      <p class="subdued" style="margin-top:16px">Illustrative simulation. No real purchase, cancellation, refund or claim will occur.</p>
      <div class="modal-actions">
        <button class="btn btn-quiet" type="button" data-close-modal>Go back</button>
        <button class="btn btn-primary" type="button" id="approvePlan">Confirm plan change</button>
      </div>
    `);

    modal.querySelector('#approvePlan').addEventListener('click', async () => {
      try {
        const result = await api.confirm(state.selectedPlanId, state.profileId, emergency);
        closeModal(false);
        showSuccess(result);
      } catch (error) {
        announce(error.message);
        closeModal();
      }
    });
  });

  el.undoButton.addEventListener('click', () => {
    resetSelection();
    announce('The simulated change was undone. Your original trip remains as it was.');
  });

  // ---------------- Countdown ----------------

  state.timer = window.setInterval(() => {
    if (state.secondsLeft > 0) state.secondsLeft -= 1;
    const minutes = Math.floor(state.secondsLeft / 60);
    el.countdown.textContent = clockTime(state.secondsLeft);
    el.countdown.setAttribute('aria-label', `${minutes} minutes remaining`);
    // Announcing every minute would trample anything else in the live region.
    if (minutes !== state.lastAnnouncedMinute && MILESTONES.includes(minutes)) {
      state.lastAnnouncedMinute = minutes;
      announce(`${minutes} minutes remaining to protect tonight's hotel booking.`);
    }
    if (state.secondsLeft === 0) window.clearInterval(state.timer);
  }, 1000);

  paint();

  return () => window.clearInterval(state.timer);
}
