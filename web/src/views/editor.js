// Stage 4 — the plan editor.
//
// The member can rearrange the plan by dragging an alternative into the booking
// it belongs to. The one architectural rule: **the frontend never decides
// whether the result works.** Every change is posted to the backend, which
// re-propagates the dependency graph, re-derives the violations and re-does the
// arithmetic. What comes back is what is displayed. If the browser were allowed
// to compute feasibility it would quietly become the source of truth, and it
// would be wrong the first time a supplier rule changed.
//
// Drag is the demonstration interaction, not the only one. Every option also has
// a plain button, because a drag-only editor is unusable by keyboard and hostile
// on a phone.

import { api } from '../api.js';
import { duration, escapeHtml, money, signedMoney, stamp, stampZoned } from '../format.js';
import { icons, kindIcon } from '../icons.js';

function optionChip(option, { chosen, currency, draggable = true }) {
  const kind = option.drops_booking ? 'critical' : option.optional ? 'accent' : 'neutral';
  return `
    <li class="opt${chosen ? ' is-chosen' : ''}" data-option="${escapeHtml(option.id)}"
        data-booking="${escapeHtml(option.booking_id)}" ${draggable ? 'draggable="true"' : ''}>
      <span class="opt-grip" aria-hidden="true">${icons.grip}</span>
      <span class="icon-badge ${kind}" aria-hidden="true">${kindIcon[option.kind] ?? icons.plane}</span>
      <span class="opt-main">
        <span class="opt-title">${escapeHtml(option.title)}</span>
        <span class="opt-meta">
          ${escapeHtml(option.supplier)} ·
          <span class="${option.cost_delta <= 0 ? 'pos' : 'neg'}">${escapeHtml(signedMoney(option.cost_delta, currency))}</span>
          ${option.start !== option.end ? ` · ${escapeHtml(stamp(option.start))}` : ''}
        </span>
        ${option.notes.length ? `<span class="opt-note">${escapeHtml(option.notes[0])}</span>` : ''}
      </span>
      <button class="btn btn-quiet opt-use" type="button" data-use="${escapeHtml(option.id)}"
              ${chosen ? 'disabled' : ''}>${chosen ? 'In plan' : 'Use'}</button>
    </li>`;
}

export function renderEditor(container, {
  planning, basePlan, currency, priority, profileId, onApprove, announce,
}) {
  const optionsById = Object.fromEntries(planning.options.map((o) => [o.id, o]));
  const bookingsById = Object.fromEntries(planning.bookings.map((b) => [b.id, b]));

  // Group the option catalogue by the booking it belongs to, in the itinerary's
  // own dependency order so the board reads down the trip.
  const groups = [];
  const order = planning.bookings.map((b) => b.id);
  const byBooking = new Map();
  planning.options.forEach((option) => {
    if (!byBooking.has(option.booking_id)) byBooking.set(option.booking_id, []);
    byBooking.get(option.booking_id).push(option);
  });
  [...byBooking.keys()]
    .sort((a, b) => {
      const ia = order.indexOf(a.replace('_supplement', ''));
      const ib = order.indexOf(b.replace('_supplement', ''));
      return ia - ib;
    })
    .forEach((bookingId) => {
      const booking = bookingsById[bookingId];
      groups.push({
        bookingId,
        label: booking
          ? booking.label
          : `Added stay for the overnight wait`,
        title: booking ? booking.title : 'Not part of the original trip',
        optional: !booking,
        options: byBooking.get(bookingId),
      });
    });

  const state = {
    selections: { ...basePlan.selections },
    plan: basePlan,
    comparison: null,
    busy: false,
  };

  container.innerHTML = `
    <div class="editor">
      <div class="editor-board" id="editorBoard"></div>
      <aside class="editor-side">
        <div class="card editor-metrics" id="editorMetrics" aria-live="polite"></div>
      </aside>
    </div>`;

  const board = container.querySelector('#editorBoard');
  const metrics = container.querySelector('#editorMetrics');

  function paintBoard() {
    board.innerHTML = groups.map((group) => {
      const chosenId = state.selections[group.bookingId];
      const chosen = optionsById[chosenId];
      return `
        <section class="lane" data-lane="${escapeHtml(group.bookingId)}" aria-labelledby="lane-${escapeHtml(group.bookingId)}">
          <div class="lane-head">
            <div>
              <p class="lane-label" id="lane-${escapeHtml(group.bookingId)}">${escapeHtml(group.label)}</p>
              <p class="lane-title">${escapeHtml(group.title)}</p>
            </div>
            ${group.optional && !chosen ? '<span class="chip chip-neutral"><span class="dot"></span>Not added</span>' : ''}
          </div>

          <div class="lane-drop" data-drop="${escapeHtml(group.bookingId)}">
            ${chosen ? `
              <div class="lane-chosen">
                <span class="icon-badge ${chosen.drops_booking ? 'critical' : 'accent'}" aria-hidden="true">${kindIcon[chosen.kind] ?? icons.plane}</span>
                <span class="opt-main">
                  <span class="opt-title">${escapeHtml(chosen.title)}</span>
                  <span class="opt-meta">${escapeHtml(chosen.detail)}</span>
                  <span class="opt-tool mono">${escapeHtml(chosen.tool_call)}</span>
                </span>
                <span class="lane-cost ${chosen.cost_delta <= 0 ? 'pos' : 'neg'}">${escapeHtml(signedMoney(chosen.cost_delta, currency))}</span>
              </div>` : `
              <p class="lane-empty">
                ${group.optional
                  ? 'Not added. Drop an option here to include it, or press <em>Use</em>.'
                  : 'Untouched — this booking stays exactly as it was. Drop an option here to change that.'}
              </p>`}
          </div>

          <ul class="opt-list">
            ${group.options.map((option) => optionChip(option, {
              chosen: option.id === chosenId, currency,
            })).join('')}
          </ul>
        </section>`;
    }).join('');
  }

  function paintMetrics() {
    const plan = state.plan;
    const m = plan.metrics;
    const cmp = state.comparison;

    const delta = (value, invert = false) => {
      if (!value) return '<span class="cmp-same">no change</span>';
      const better = invert ? value > 0 : value < 0;
      const sign = value > 0 ? '+' : '−';
      return `<span class="${better ? 'pos' : 'neg'}">${sign}${Math.abs(value)}</span>`;
    };

    metrics.innerHTML = `
      <div class="card-head">
        <div>
          <p class="eyebrow">Recomputed by the server</p>
          <h4 class="stage-title">${escapeHtml(plan.name)}</h4>
        </div>
        <span class="chip chip-${plan.valid ? 'good' : 'critical'}">
          <span class="dot"></span>${plan.valid ? 'Workable' : 'Blocked'}
        </span>
      </div>

      <dl class="metric-list">
        <div><dt>Whole-trip money</dt><dd class="${m.cost_delta <= 0 ? 'pos' : 'neg'}">${escapeHtml(signedMoney(m.cost_delta, currency))}</dd></div>
        <div><dt>Trip time given up</dt><dd>${escapeHtml(duration(m.hours_lost))}</dd></div>
        <div><dt>Bookings re-transacted</dt><dd>${m.bookings_changed}</dd></div>
        <div><dt>Written off</dt><dd class="${m.forfeited ? 'neg' : ''}">${escapeHtml(money(m.forfeited, currency))}</dd></div>
        <div><dt>Refund expected</dt><dd class="${m.refund_expected ? 'pos' : ''}">${escapeHtml(money(m.refund_expected, currency))}</dd></div>
        <div><dt>Arrival</dt><dd>${escapeHtml(stampZoned(m.arrival))}</dd></div>
      </dl>

      ${cmp ? `
        <p class="cmp-line">
          Against ${escapeHtml(basePlan.name)}:
          money ${delta(cmp.cost_delta)},
          hours ${delta(cmp.hours_delta)},
          bookings touched ${delta(cmp.changed_delta)}.
        </p>` : ''}

      ${plan.violations.length ? `
        <ul class="plan-violations">
          ${plan.violations.map((v) => `
            <li class="viol-${escapeHtml(v.severity)}">
              <strong>${v.severity === 'hard' ? 'Blocks the plan' : 'Tight'}</strong>
              ${escapeHtml(bookingsById[v.booking_id]?.title ?? v.booking_id)} — ${escapeHtml(v.message)}
              ${v.edge ? `<span class="viol-edge">${escapeHtml(v.edge.rationale)}</span>` : ''}
            </li>`).join('')}
        </ul>` : `
        <p class="viol-clear">
          <span class="icon-badge good" aria-hidden="true">${icons.check}</span>
          Every hard dependency on the trip is satisfied by this arrangement.
        </p>`}

      <p class="plan-score">${escapeHtml(plan.score_breakdown)}</p>

      <button class="btn btn-primary btn-block" type="button" id="approveEdited"
              ${plan.valid && !state.busy ? '' : 'disabled'}>
        Review and approve
      </button>
      <p class="subdued" style="margin-top:12px">
        Nothing is transacted by approving. Approval freezes a snapshot and queues the steps.
      </p>`;

    metrics.querySelector('#approveEdited').addEventListener('click', () => onApprove(state.plan));
  }

  async function apply(optionId, bookingId) {
    const option = optionsById[optionId];
    if (!option) return;
    if (option.booking_id !== bookingId) {
      announce(`${option.title} belongs to a different booking and cannot go there.`);
      return;
    }
    if (state.selections[bookingId] === optionId) return;

    state.selections = { ...state.selections, [bookingId]: optionId };
    paintBoard();
    state.busy = true;

    try {
      const result = await api.validate(state.selections, {
        priority, profileId, basePlanId: basePlan.id,
      });
      state.plan = result.plan;
      state.comparison = result.comparison;
      announce(
        `${option.title} applied. ${result.plan.valid
          ? 'The plan still satisfies every hard dependency.'
          : `${result.plan.violations.filter((v) => v.severity === 'hard').length} hard dependencies are now unsatisfied.`}`,
      );
    } catch (error) {
      announce(error.message);
    } finally {
      state.busy = false;
      paintMetrics();
    }
  }

  // -- interaction -------------------------------------------------------

  board.addEventListener('click', (event) => {
    const button = event.target.closest('[data-use]');
    if (!button) return;
    const item = button.closest('[data-option]');
    apply(button.dataset.use, item.dataset.booking);
  });

  board.addEventListener('dragstart', (event) => {
    const item = event.target.closest('[data-option]');
    if (!item) return;
    event.dataTransfer.setData('text/plain', item.dataset.option);
    event.dataTransfer.effectAllowed = 'move';
    item.classList.add('is-dragging');
    board.querySelectorAll(`[data-drop="${item.dataset.booking}"]`).forEach((zone) => zone.classList.add('is-target'));
  });

  board.addEventListener('dragend', () => {
    board.querySelectorAll('.is-dragging').forEach((el) => el.classList.remove('is-dragging'));
    board.querySelectorAll('.is-target, .is-over').forEach((el) => el.classList.remove('is-target', 'is-over'));
  });

  board.addEventListener('dragover', (event) => {
    const zone = event.target.closest('[data-drop]');
    if (!zone) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    zone.classList.add('is-over');
  });

  board.addEventListener('dragleave', (event) => {
    event.target.closest('[data-drop]')?.classList.remove('is-over');
  });

  board.addEventListener('drop', (event) => {
    const zone = event.target.closest('[data-drop]');
    if (!zone) return;
    event.preventDefault();
    zone.classList.remove('is-over');
    apply(event.dataTransfer.getData('text/plain'), zone.dataset.drop);
  });

  paintBoard();
  paintMetrics();

  return {
    get plan() { return state.plan; },
  };
}
