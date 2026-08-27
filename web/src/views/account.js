// Signed-in account overview: the calm state, before anything goes wrong.
// Layout follows DESIGN.md — card art and the primary figure lead, everything
// else is a clean stack of white cards on --surface-1.

import { escapeHtml, money, points } from '../format.js';
import { icons } from '../icons.js';
import { openModal } from '../components/modal.js';
import { mountIsland } from '../islands/mount.js';
import AreaTrendChart from '../islands/AreaTrendChart.jsx';

// A six-month Membership Rewards trend ending at the live balance. Synthetic,
// deterministic, and clearly labelled — it exists to give the overview a real
// chart rather than a decorative one.
function rewardsTrend(balance) {
  const months = ['Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug'];
  const steps = [0.87, 0.9, 0.93, 0.96, 0.98, 1];
  return months.map((label, i) => ({ label, value: Math.round(balance * steps[i]) }));
}

function componentBlock(booking, currency) {
  return `
    <section class="detail-block">
      <h4>${escapeHtml(booking.label)}</h4>
      <div class="detail-line"><span>Booking</span><strong>${escapeHtml(booking.title)}</strong></div>
      <div class="detail-line"><span>Detail</span><strong>${escapeHtml(booking.detail)}</strong></div>
      <div class="detail-line"><span>Supplier</span><strong>${escapeHtml(booking.supplier)}</strong></div>
      <div class="detail-line"><span>Charged</span><strong>${money(booking.amount, currency)}</strong></div>
    </section>`;
}

function costBlock(trip, currency) {
  const lines = trip.bookings
    .map((booking) => `
      <div class="detail-line">
        <span>${escapeHtml(booking.label)}</span>
        <strong>${money(booking.amount, currency)}</strong>
      </div>`)
    .join('');

  return `
    <section class="detail-block">
      <h4>Cost summary · ${escapeHtml(currency)}</h4>
      ${lines}
      <div class="detail-line"><span>Taxes and fees</span><strong>${money(trip.taxes_and_fees, currency)}</strong></div>
      <div class="detail-line total"><span>Total trip cost</span><strong>${money(trip.total, currency)}</strong></div>
    </section>`;
}

function transactionRow(txn, currency) {
  const credit = txn.amount < 0;
  return `
    <li>
      <span>
        <span class="txn-merchant">${escapeHtml(txn.merchant)}</span>
        <span class="txn-meta">${escapeHtml(txn.category)} · ${escapeHtml(txn.date)} · ${escapeHtml(txn.status)}</span>
      </span>
      <span class="txn-amount${credit ? ' pos' : ''}">${credit ? '−' : ''}${money(Math.abs(txn.amount), currency)}</span>
    </li>`;
}

/** Shut every open disclosure in `root`: the trip panel and any <details>. */
export function collapseDisclosures(root) {
  if (!root) return;
  root.querySelectorAll('[aria-expanded="true"]').forEach((toggle) => {
    toggle.setAttribute('aria-expanded', 'false');
    const panel = toggle.getAttribute('aria-controls')
      ? root.querySelector(`#${CSS.escape(toggle.getAttribute('aria-controls'))}`)
      : null;
    panel?.classList.remove('is-open');
  });
  root.querySelectorAll('details[open]').forEach((d) => d.removeAttribute('open'));
}

export function renderAccount(container, { data, onCheckFlights, announce }) {
  const { member, trip, transactions, benefits, currency } = data;
  const bookingBlocks = trip.bookings
    .map((booking) => componentBlock(booking, currency))
    .join('');

  container.innerHTML = `
    <div class="page-head">
      <p class="eyebrow">Good evening</p>
      <h2>Welcome back, ${escapeHtml(member.first_name)}.</h2>
      <p>Your travel and rewards, at a glance.</p>
    </div>

    <div class="account-grid">
      <div class="card-art">
        <div class="card-art-top">
          <span class="card-tier"><span class="tier-rule" aria-hidden="true"></span>${escapeHtml(member.tier)}</span>
          <span class="brand-mark" aria-hidden="true">SL</span>
        </div>
        <div>
          <h3>${escapeHtml(member.card_label)}</h3>
          <p class="meta" style="margin-top:4px">${escapeHtml(member.name)} · Member since ${escapeHtml(member.member_since)}</p>
          <p class="card-number" style="margin-top:12px">•••• •••• •••• ${escapeHtml(member.card_last_four)}</p>
        </div>
      </div>

      <div class="card rewards-card">
        <div>
          <p class="eyebrow">Membership Rewards®</p>
          <span class="figure-lead" style="margin-top:8px">${points(member.rewards_points)}</span>
          <p class="rewards-caption">points available to redeem or transfer</p>
        </div>
        <div class="divider"></div>
        <div class="trip-strip">
          <div>
            <p class="eyebrow">Travel credit</p>
            <p class="figure" style="margin-top:4px">${money(member.travel_credit, currency)} available</p>
          </div>
          <button class="btn btn-ghost" id="benefitsButton" type="button">Explore your benefits</button>
        </div>
      </div>
    </div>

    <section class="card" aria-labelledby="trendHeading">
      <div class="card-head">
        <div>
          <p class="eyebrow" id="trendHeading">Membership Rewards®</p>
          <h3 style="margin-top:8px;font-size:var(--text-section)">Points, last 6 months</h3>
        </div>
        <span class="chip chip-accent"><span class="dot"></span>Growing</span>
      </div>
      <div id="rewardsChart" style="margin-top:16px"></div>
    </section>

    <section class="card" aria-labelledby="tripHeading">
      <div class="card-head">
        <div>
          <p class="eyebrow" id="tripHeading">Upcoming trip · ${escapeHtml(trip.dates.split(', ')[0])} September</p>
          <div class="route" style="margin-top:8px">
            <span class="route-code">${escapeHtml(trip.origin.code)}</span>
            <span class="route-line" aria-hidden="true"></span>
            <span class="route-code">${escapeHtml(trip.destination.code)}</span>
            <span class="route-line" aria-hidden="true"></span>
            <span class="route-code">${escapeHtml(trip.onward.code)}</span>
          </div>
        </div>
        <span class="chip chip-good"><span class="dot"></span>Confirmed</span>
      </div>

      <button class="trip-toggle" id="tripToggle" type="button" aria-expanded="false" aria-controls="tripPanel">
        <span class="icon-badge" aria-hidden="true">${icons.plane}</span>
        <span>
          <span style="display:block;font-weight:600;font-size:var(--text-sm)">${escapeHtml(trip.origin.city)} → ${escapeHtml(trip.destination.city)} → ${escapeHtml(trip.onward.city)}</span>
          <span style="display:block;font-size:var(--text-xs);color:var(--ink-muted)">${escapeHtml(trip.dates)} · ${escapeHtml(trip.cabin)} · ${trip.bookings.length} bookings · ${money(trip.total, currency)}</span>
        </span>
        <span class="expand" aria-hidden="true">${icons.chevron}</span>
      </button>

      <div class="trip-panel" id="tripPanel">
        <div>
          <div class="detail-grid">
            ${bookingBlocks}
            ${costBlock(trip, currency)}
          </div>
          <p class="subdued" style="margin-top:16px">Illustrative booking and pricing data for this demonstration.</p>
        </div>
      </div>
    </section>

    <div class="account-grid">
      <section class="card" aria-labelledby="txnHeading">
        <h3 id="txnHeading" style="font-size:var(--text-section)">Recent transactions</h3>
        <ul class="txn-list" style="margin-top:8px">
          ${transactions.map((txn) => transactionRow(txn, currency)).join('')}
        </ul>
      </section>

      <section class="card" aria-labelledby="protectHeading">
        <div class="card-head">
          <div>
            <p class="eyebrow">Trip protection</p>
            <h3 id="protectHeading" style="margin-top:8px;font-size:var(--text-section)">Every booking is on one Card</h3>
          </div>
          <span class="icon-badge" aria-hidden="true">${icons.shield}</span>
        </div>
        <p class="muted" style="margin-top:12px;font-size:var(--text-sm)">
          All ${trip.bookings.length} bookings are on ${escapeHtml(member.card_label)}, that's what lets
          Soft Landing maps the dependencies and prices a disruption against the whole journey, not one booking.
        </p>
        <div class="disruption-cta" style="margin-top:24px">
          <p class="subdued">Check this trip for disruption.</p>
          <button class="btn btn-primary" id="checkFlightsButton" type="button">Check my flights</button>
        </div>
      </section>
    </div>

    <div class="benefits-strip on-navy">
      <div>
        <strong>More value is waiting.</strong>
        <p>Lounge access, travel credits and protections selected for this membership.</p>
      </div>
      <button class="btn btn-on-navy" id="benefitsButtonAlt" type="button">See all ${benefits.length} benefits</button>
    </div>
  `;

  // Expandable itinerary.
  const toggle = container.querySelector('#tripToggle');
  const panel = container.querySelector('#tripPanel');
  toggle.addEventListener('click', () => {
    const open = toggle.getAttribute('aria-expanded') !== 'true';
    toggle.setAttribute('aria-expanded', String(open));
    panel.classList.toggle('is-open', open);
    announce(open ? 'Travel details expanded.' : 'Travel details collapsed.');
  });

  const showBenefits = (event) => {
    openModal(event.currentTarget, `
      <p class="eyebrow">Your membership</p>
      <h2 id="modalTitle">Benefits that travel with you</h2>
      <p class="modal-intro">A selection of benefits connected to this illustrative account.</p>
      <div class="benefit-list">
        ${benefits.map((benefit) => `
          <div class="benefit-item">
            <span class="icon-badge" aria-hidden="true">${icons.star}</span>
            <div>
              <strong>${escapeHtml(benefit.title)}</strong>
              <span>${escapeHtml(benefit.detail)}</span>
            </div>
          </div>`).join('')}
      </div>
      <div class="modal-actions"><button class="btn btn-primary" type="button" data-close-modal>Done</button></div>
    `);
    announce('Benefits explorer opened.');
  };

  container.querySelector('#benefitsButton').addEventListener('click', showBenefits);
  container.querySelector('#benefitsButtonAlt').addEventListener('click', showBenefits);
  container.querySelector('#checkFlightsButton').addEventListener('click', () => {
    // Collapse the itinerary on the way out. Leaving it open means coming back
    // to a screen still scrolled around an expanded panel the member has
    // finished with, and the recovery console repeats the same bookings anyway.
    collapseDisclosures(container);
    onCheckFlights();
  });

  mountIsland(container.querySelector('#rewardsChart'), AreaTrendChart, {
    data: rewardsTrend(member.rewards_points),
    prefix: '',
  });
}
