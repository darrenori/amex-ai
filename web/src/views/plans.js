// Stage 3 — candidate plans, compared as whole answers.
//
// Every card here is a complete, internally coherent recovery: one option chosen
// for each affected booking, assembled end to end for a single objective. That
// matters. Taking each agent's single best option and stapling them together
// produces a plan nobody chose — the cheapest flight beside the least-disruptive
// hotel change beside the fastest transfer — and its totals do not describe any
// trip the member could actually take.
//
// The Pareto badge is the honest part of the ranking: it needs no weights at all
// and says only that nothing else beats this plan on every objective at once.

import { duration, escapeHtml, money, signedMoney, stampZoned } from '../format.js';
import { icons, kindIcon } from '../icons.js';
import { collectLinks, partnerLinksMarkup } from './partners.js';

const OBJECTIVE_DOMAIN = { cost: 700, hours: 48, changed: 7, risk: 0.5 };

function amexPartnerBadge(partner) {
  if (!partner) return '';
  const program = partner.program ?? partner.amex_program ?? 'Amex partner';
  return `<span class="amex-partner-badge" title="Verified against the curated Amex partner catalog">${escapeHtml(program)}</span>`;
}

function amexPartnerLinks(options) {
  const seen = new Map();
  options.forEach((option) => {
    const partner = option.amex_partner;
    if (!partner) return;
    const url = partner.official_url ?? partner.url;
    if (!url || seen.has(url)) return;
    const name = partner.canonical_name ?? partner.name ?? option.supplier;
    seen.set(url, { label: `${name} · ${partner.program ?? partner.amex_program ?? 'Amex partner'}`, url });
  });
  return [...seen.values()];
}

function aiExplanationMarkup(ai) {
  if (!ai || ai.status !== 'generated') return '';
  const tools = Array.isArray(ai.tools_used) ? ai.tools_used : [];
  const latency = Number.isFinite(Number(ai.latency_ms)) ? `${Math.round(Number(ai.latency_ms))} ms` : '';
  const metadata = [ai.provider, ai.model, tools.length ? `${tools.length} MCP tools` : '', latency].filter(Boolean);
  const rationale = ai.ranking_rationale ?? ai.rationale ?? '';
  const memberExplanation = ai.member_explanation ?? ai.explanation ?? '';

  return `
    <aside class="ai-explanation" aria-label="AI explanation of the deterministic recommendation">
      <div class="ai-explanation-head">
        <div>
          <span class="ai-kicker">Model-assisted explanation</span>
          <strong>Context for the deterministic recommendation</strong>
        </div>
        ${metadata.length ? `<span class="ai-meta">${metadata.map(escapeHtml).join(' · ')}</span>` : ''}
      </div>
      ${rationale ? `<p>${escapeHtml(rationale)}</p>` : ''}
      ${memberExplanation && memberExplanation !== rationale ? `<p class="ai-member-copy">${escapeHtml(memberExplanation)}</p>` : ''}
      ${tools.length ? `<p class="ai-tools"><span>Read-only context:</span> ${tools.map((tool) => `<code>${escapeHtml(tool?.name ?? tool)}</code>`).join(' ')}</p>` : ''}
    </aside>`;
}

function bar(value, domain, tone) {
  const width = Math.min(Math.abs(value) / domain, 1) * 100;
  return `<span class="obj-track"><span class="obj-fill ${tone}" style="width:${width.toFixed(1)}%"></span></span>`;
}

function planCard(plan, { recommended, currency, optionsById, codes = [] }) {
  const m = plan.metrics;
  const touched = Object.entries(plan.selections)
    .map(([bookingId, optionId]) => ({ bookingId, option: optionsById[optionId] }))
    .filter((row) => row.option && (row.option.changes_booking || row.option.optional));

  const badges = [
    recommended ? '<span class="badge-rec">Recommended</span>' : '',
    plan.pareto_optimal ? '<span class="badge-pareto">Pareto-optimal</span>' : '',
    !plan.valid ? '<span class="badge-invalid">Unworkable</span>' : '',
  ].filter(Boolean).join('');

  return `
    <article class="card plan-card${recommended ? ' is-recommended' : ''}${plan.valid ? '' : ' is-invalid'}"
             data-plan="${escapeHtml(plan.id)}">
      ${badges ? `<p class="plan-badges">${badges}</p>` : ''}

      <div class="plan-top">
        <span class="icon-badge" aria-hidden="true">${icons.plane}</span>
        <div style="min-width:0">
          <h4 class="plan-heading">${escapeHtml(plan.name)}</h4>
          <p class="plan-summary">${escapeHtml(plan.summary)}</p>
        </div>
      </div>

      <dl class="obj-grid">
        <div>
          <dt>Whole-trip money</dt>
          <dd class="${m.cost_delta <= 0 ? 'pos' : 'neg'}">${escapeHtml(signedMoney(m.cost_delta, currency))}</dd>
          ${bar(m.cost_delta, OBJECTIVE_DOMAIN.cost, m.cost_delta <= 0 ? 'good' : 'warn')}
        </div>
        <div>
          <dt>Trip time given up</dt>
          <dd>${escapeHtml(duration(m.hours_lost))}</dd>
          ${bar(m.hours_lost, OBJECTIVE_DOMAIN.hours, 'warn')}
        </div>
        <div>
          <dt>Bookings re-transacted</dt>
          <dd>${m.bookings_changed}</dd>
          ${bar(m.bookings_changed, OBJECTIVE_DOMAIN.changed, 'warn')}
        </div>
        <div>
          <dt>Experience given up</dt>
          <dd class="${m.experience_lost ? 'neg' : ''}">${m.experience_lost ? escapeHtml(money(m.experience_lost, currency)) : 'None'}</dd>
          ${bar(m.experience_lost, OBJECTIVE_DOMAIN.cost, 'crit')}
        </div>
        <div>
          <dt>Chance of failing on the day</dt>
          <dd class="${m.reliability_risk > 0.25 ? 'neg' : ''}">${Math.round(m.reliability_risk * 100)}%</dd>
          ${bar(m.reliability_risk, OBJECTIVE_DOMAIN.risk, m.reliability_risk > 0.25 ? 'crit' : 'warn')}
        </div>
      </dl>

      <hr class="divider">

      <p class="plan-arrival">
        Arrives <strong>${escapeHtml(stampZoned(m.arrival))}</strong>
        ${m.forfeited ? `· <span class="neg">${escapeHtml(money(m.forfeited, currency))} written off</span>` : ''}
        ${m.refund_expected ? `· <span class="pos">${escapeHtml(money(m.refund_expected, currency))} back</span>` : ''}
      </p>

      <ul class="plan-touch">
        ${touched.map(({ option }) => `
          <li>
            <span class="icon-badge ${option.drops_booking ? 'critical' : 'neutral'}" aria-hidden="true">${kindIcon[option.kind] ?? icons.plane}</span>
            <span class="touch-main">
              <span class="touch-title">${escapeHtml(option.title)}</span>
              <span class="touch-meta">
                ${escapeHtml(option.supplier)} · ${escapeHtml(signedMoney(option.cost_delta, currency))}
                ${amexPartnerBadge(option.amex_partner)}
              </span>
            </span>
          </li>`).join('')}
      </ul>

      ${plan.violations.length ? `
        <ul class="plan-violations">
          ${plan.violations.map((v) => `
            <li class="viol-${escapeHtml(v.severity)}">
              <strong>${v.severity === 'hard' ? 'Blocks the plan' : 'Tight'}</strong>
              ${escapeHtml(v.message)}
            </li>`).join('')}
        </ul>` : ''}

      ${codes.length ? `
        <ul class="code-list" aria-label="Reason codes for ${escapeHtml(plan.name)}">
          ${codes.map((code) => `<li class="code-chip">${escapeHtml(code.replace(/_/g, ' ').toLowerCase())}</li>`).join('')}
        </ul>` : ''}

      <p class="plan-score">${escapeHtml(plan.score_breakdown)}</p>

      ${partnerLinksMarkup([
        ...collectLinks(touched.map((t) => t.option)),
        ...amexPartnerLinks(touched.map((t) => t.option)),
      ], { label: 'With your Card' })}

      <div class="plan-actions">
        <button class="btn btn-ghost" type="button" data-edit-plan="${escapeHtml(plan.id)}">Adjust this plan</button>
        <button class="btn ${recommended ? 'btn-primary' : 'btn-quiet'}" type="button"
                data-choose-plan="${escapeHtml(plan.id)}" ${plan.valid ? '' : 'disabled'}>
          Review and approve
        </button>
      </div>
    </article>`;
}

export function weightingMarkup(ranking, profiles, activeProfileId, activePriority) {
  const presets = ranking.presets.map((preset) => `
    <button class="toggle-btn${activePriority === preset.id ? ' is-active' : ''}" type="button"
            data-priority="${escapeHtml(preset.id)}" aria-pressed="${activePriority === preset.id}">
      <span class="toggle-head"><span class="toggle-name">${escapeHtml(preset.label)}</span></span>
      <span class="toggle-desc">${escapeHtml(preset.description)}</span>
    </button>`).join('');

  return `
    <div class="weight-block">
      <p class="weight-lead">
        The member never sees this control. Their weighting is already inferred from what they chose
        the last time they had this trade-off in front of them — the presets exist so a reviewer can
        watch the same cancellation resolve differently.
      </p>
      <div class="toggle-group" role="group" aria-label="Ranking objective">
        <button class="toggle-btn${activePriority === 'inferred' ? ' is-active' : ''}" type="button"
                data-priority="inferred" aria-pressed="${activePriority === 'inferred'}">
          <span class="toggle-head">${icons.star}<span class="toggle-name">Inferred from history</span></span>
          <span class="toggle-desc">what this member's own past choices imply</span>
        </button>
        ${presets}
      </div>

      <div class="toggle-group toggle-group-sub${activePriority === 'inferred' ? '' : ' is-dimmed'}"
           role="group" aria-label="Inferred traveler profile">
        ${profiles.map((profile) => `
          <button class="toggle-btn${profile.id === activeProfileId ? ' is-active' : ''}" type="button"
                  data-profile="${escapeHtml(profile.id)}" aria-pressed="${profile.id === activeProfileId}"
                  ${activePriority === 'inferred' ? '' : 'disabled'}>
            <span class="toggle-head">${icons[profile.icon] ?? icons.clock}<span class="toggle-name">${escapeHtml(profile.name)}</span></span>
            <span class="toggle-desc">${escapeHtml(profile.description)}</span>
          </button>`).join('')}
      </div>

      <p class="weight-line">Weighting in force: <strong>${escapeHtml(ranking.weights.description)}</strong></p>
      <p class="formula">${escapeHtml(ranking.formula)}</p>
    </div>`;
}

export function historyMarkup(profile) {
  if (!profile) return '';
  return `
    <div class="history-panel">
      <p class="history-head">Past choices this weighting was regressed from (synthetic, for demonstration)</p>
      ${profile.history.map((item) => `
        <div class="history-item">
          <span class="history-when">${escapeHtml(item.when)}</span>
          <span class="history-text">${escapeHtml(item.text)}</span>
        </div>`).join('')}
    </div>`;
}

export function plansMarkup(plans, ranking, currency, optionsById) {
  const byId = Object.fromEntries(plans.map((p) => [p.id, p]));
  const ordered = ranking.order.map((id) => byId[id]).filter(Boolean);

  return `
    <div class="notif">
      <span class="notif-icon" aria-hidden="true">TS</span>
      <div style="flex:1;min-width:0">
        <div class="notif-top"><span class="notif-app">TripShield</span><span class="notif-time">now</span></div>
        <p class="notif-msg">${escapeHtml(ranking.notification)}</p>
      </div>
    </div>

    <p class="explain">${escapeHtml(ranking.explanation)}</p>

    ${aiExplanationMarkup(ranking.ai)}

    <div class="plan-grid">
      ${ordered.map((plan) => planCard(plan, {
        recommended: plan.id === ranking.recommended_plan_id,
        currency,
        optionsById,
        codes: ranking.reason_codes?.[plan.id] ?? [],
      })).join('')}
    </div>`;
}
