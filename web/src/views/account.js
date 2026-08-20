// Signed-in account overview: the calm state, before anything goes wrong.
// Layout follows DESIGN.md — card art and the primary figure lead, everything
// else is a clean stack of white cards on --surface-1.

import { escapeHtml, money, points } from '../format.js';
import { icons } from '../icons.js';
import { openModal } from '../components/modal.js';

function componentBlock(component, currency) {
  return `
    <section class="detail-block">
      <h4>${escapeHtml(component.label)}</h4>
      <div class="detail-line"><span>Booking</span><strong>${escapeHtml(component.title)}</strong></div>
      <div class="detail-line"><span>Detail</span><strong>${escapeHtml(component.detail)}</strong></div>
      <div class="detail-line"><span>Charged</span><strong>${money(component.amount, currency)}</strong></div>
    </section>`;
}

function costBlock(trip, currency) {
  const lines = trip.components
    .map((component) => `
      <div class="detail-line">
        <span>${escapeHtml(component.label)}</span>
        <strong>${money(component.amount, currency)}</strong>
      </div>`)
    .join('');

  return `
    <section class="detail-block">
      <h4>Cost summary · ${escapeHtml(currency)}</h4>
      ${lines}
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

export function renderAccount(container, { data, onSimulate, announce }) {
  const { member, trip, transactions, benefits, currency } = data;
  const bookingBlocks = trip.components
    .filter((component) => component.kind !== 'fees')
    .map((component) => componentBlock(component, currency))
    .join('');

  container.innerHTML = `
    <div class="page-head">
      <p class="eyebrow">Good evening</p>
      <h2>Welcome back, ${escapeHtml(member.first_name)}.</h2>
      <p>Your travel, rewards and recent account activity are all in order.</p>
    </div>

    <div class="account-grid">
      <div class="card-art">
        <div class="card-art-top">
          <span class="card-tier"><span class="tier-rule" aria-hidden="true"></span>${escapeHtml(member.tier)}</span>
          <span class="brand-mark" aria-hidden="true">TS</span>
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

    <section class="card" aria-labelledby="tripHeading">
      <div class="card-head">
        <div>
          <p class="eyebrow" id="tripHeading">Upcoming trip · ${escapeHtml(trip.dates.split('–')[0])} September</p>
          <div class="route" style="margin-top:8px">
            <span class="route-code">${escapeHtml(trip.origin.code)}</span>
            <span class="route-line" aria-hidden="true"></span>
            <span class="route-code">${escapeHtml(trip.destination.code)}</span>
          </div>
        </div>
        <span class="chip chip-good"><span class="dot"></span>Confirmed</span>
      </div>

      <button class="trip-toggle" id="tripToggle" type="button" aria-expanded="false" aria-controls="tripPanel">
        <span class="icon-badge" aria-hidden="true">${icons.plane}</span>
        <span>
          <span style="display:block;font-weight:600;font-size:var(--text-sm)">${escapeHtml(trip.origin.city)} → ${escapeHtml(trip.destination.city)}</span>
          <span style="display:block;font-size:var(--text-xs);color:var(--ink-muted)">${escapeHtml(trip.dates)} · ${escapeHtml(trip.cabin)} · ${trip.nights} nights · ${money(trip.total, currency)}</span>
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
          Flight, hotel and car rental for this trip were all charged to ${escapeHtml(member.card_label)}.
          That is what lets TripShield price a disruption against the whole journey instead of one booking.
        </p>
        <div class="disruption-cta" style="margin-top:24px">
          <p class="subdued">Run the demonstration scenario.</p>
          <button class="btn btn-primary" id="simulateButton" type="button">Simulate cancellation</button>
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
  container.querySelector('#simulateButton').addEventListener('click', onSimulate);
}
