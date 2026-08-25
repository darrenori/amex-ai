import assert from 'node:assert/strict';
import test from 'node:test';

import { optionServiceLink, optionServiceLinkMarkup } from '../src/views/partners.js';
import { plansMarkup } from '../src/views/plans.js';

test('service links stay on approved Amex HTTPS destinations and open safely', () => {
  const flight = {
    kind: 'flight',
    title: 'SQ12 replacement',
    supplier: 'Singapore Airlines',
  };
  const hotel = {
    kind: 'lodging',
    title: 'Keep Hilton Tokyo Bay',
    supplier: 'Hilton Tokyo Bay',
    amex_partner: {
      official_url: 'https://www.americanexpress.com/en-sg/travel/discover/property-results/tokyo',
    },
  };

  // The booking application, not the page describing it. Note the host: it is
  // americanexpress.com.sg, which is not a subdomain of americanexpress.com and
  // so has to be allowlisted in its own right.
  assert.equal(
    optionServiceLink(flight).url,
    'https://travel.americanexpress.com.sg/shopping/',
  );
  assert.equal(
    optionServiceLink(hotel).url,
    'https://www.americanexpress.com/en-sg/travel/discover/property-results/tokyo',
  );

  const markup = optionServiceLinkMarkup(flight);
  assert.match(markup, /target="_blank"/);
  assert.match(markup, /rel="noopener noreferrer"/);
});

test('unapproved or unrelated supplier destinations never become option links', () => {
  const unsafeHotel = {
    kind: 'lodging',
    title: 'Unsafe hotel URL',
    supplier: 'Example Hotel',
    amex_partner: { official_url: 'https://evil.example/checkout' },
  };
  const activityWithUnrelatedHotelLink = {
    kind: 'activity',
    title: 'Move the park passport',
    links: [{
      label: 'Unrelated hotel search',
      url: 'https://www.americanexpress.com/en-sg/travel/hotels/',
    }],
  };

  // The attacker-controlled partner URL is discarded and the safe default is
  // used instead. That substitution is the assertion that matters here.
  assert.equal(
    optionServiceLink(unsafeHotel).url,
    'https://travel.americanexpress.com.sg/shopping/',
  );
  assert.doesNotMatch(optionServiceLink(unsafeHotel).url, /evil\.example/);
  assert.equal(optionServiceLink(activityWithUnrelatedHotelLink), null);
  assert.equal(optionServiceLinkMarkup(activityWithUnrelatedHotelLink), 'Move the park passport');
});

test('member recommendations render only the three highest-ranked complete plans', () => {
  const plans = ['one', 'two', 'three', 'four'].map((id, index) => ({
    id: `plan_${id}`,
    name: `Plan ${id}`,
    summary: `Complete plan ${id}`,
    valid: true,
    selections: {},
    violations: [],
    metrics: {
      cost_delta: index * 10,
      hours_lost: index,
      bookings_changed: 1,
      experience_lost: 0,
      reliability_risk: 0.1,
      arrival: '2026-09-18T21:50:00+09:00',
      forfeited: 0,
      refund_expected: 0,
    },
    score_breakdown: `Score ${index}`,
  }));
  const ranking = {
    notification: 'Three options are ready.',
    explanation: 'Ranked as complete trips.',
    recommended_plan_id: 'plan_one',
    order: plans.map((plan) => plan.id),
    reason_codes: {},
    ai: {
      status: 'generated',
      confidence: 0.9,
      ranking_rationale: 'The first plan is the strongest fit.',
      member_explanation: 'It best matches this member.',
      tradeoffs: plans.map((plan) => ({
        plan_id: plan.id,
        label: plan.name,
        reason: `Reason for ${plan.id}`,
      })),
    },
  };

  const markup = plansMarkup(plans, ranking, 'SGD', {});

  assert.equal((markup.match(/class="card plan-card/g) ?? []).length, 3);
  assert.match(markup, /Plan one/);
  assert.match(markup, /Plan three/);
  assert.doesNotMatch(markup, /Plan four/);
  assert.doesNotMatch(markup, /Reason for plan_four/);
});
