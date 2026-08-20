// Section 3, lower half — the inspection view.
//
// The traveler never sees a profile picker. The weight is inferred from choices
// they already made; the toggle here exists so a reviewer can watch the same
// disruption resolve differently for a different real history.

import { escapeHtml, money, signedMoney } from '../format.js';
import { icons } from '../icons.js';

// Fixed domain so bar widths stay comparable when the profile changes.
const SCORE_DOMAIN = 2400;

/** Escape first, then bold the winning plan's name where it appears in prose. */
function emphasise(sentence, name) {
  const safe = escapeHtml(sentence);
  const target = escapeHtml(name);
  return safe.replace(target, `<strong>${target}</strong>`);
}

export function profileToggleMarkup(profiles, activeId) {
  return profiles
    .map((profile) => `
      <button class="toggle-btn${profile.id === activeId ? ' is-active' : ''}" type="button"
              data-profile="${escapeHtml(profile.id)}" aria-pressed="${profile.id === activeId}"
              aria-label="${escapeHtml(profile.name)} profile — ${escapeHtml(profile.description)}">
        <span class="toggle-head">${icons[profile.icon] ?? icons.clock}<span class="toggle-name">${escapeHtml(profile.name)}</span></span>
        <span class="toggle-desc">${escapeHtml(profile.description)}</span>
      </button>`)
    .join('');
}

export function intelligenceMarkup(recovery, currency) {
  const { profile, scored, recommended_plan_id: recommendedId } = recovery;

  const history = profile.history
    .map((item) => `
      <div class="history-item">
        <span class="history-when">${escapeHtml(item.when)}</span>
        <span class="history-text">${escapeHtml(item.text)}</span>
      </div>`)
    .join('');

  const ranked = [...scored].sort((a, b) => a.score - b.score);
  const winningName = ranked[0].plan.name;

  const rows = ranked
    .map((row) => {
      const winner = row.plan.id === recommendedId;
      // Scores can be negative when the plan nets a saving; clamp the bar at zero
      // width rather than letting it invert.
      const width = Math.min(Math.max(row.score, 0) / SCORE_DOMAIN, 1) * 100;
      return `
        <div class="score-item${winner ? ' is-winner' : ''}">
          <div class="score-row">
            <div class="score-name">
              ${escapeHtml(row.plan.name)}
              ${winner ? '<span class="badge-rec">Recommended</span>' : ''}
            </div>
            <div class="score-track"><div class="score-fill" style="width:${width.toFixed(1)}%"></div></div>
            <div class="score-figure">${money(row.score, currency)}</div>
          </div>
          <p class="score-breakdown">
            Financial <strong>${signedMoney(row.net_impact, currency)}</strong>
            &nbsp;+&nbsp; Time <strong>${row.plan.hours_lost}h × ${escapeHtml(currency)} ${profile.weight}/hr
            = ${money(row.time_cost, currency)}</strong>
            &nbsp;+&nbsp; Reliability <strong>${Math.round(row.plan.reliability_risk * 100)}% risk
            = ${money(row.reliability_penalty ?? 0, currency)}</strong>
          </p>
        </div>`;
    })
    .join('');

  return `
    <p class="weight-line">Inferred time-value weight for this history: <strong>${escapeHtml(currency)} ${profile.weight} / hour</strong></p>

    <div class="history-panel">
      <p class="history-head">Past choices this weight was inferred from (synthetic, for demonstration)</p>
      ${history}
    </div>

    <div class="score-list">${rows}</div>

    <p class="explain">${emphasise(recovery.explanation, winningName)}</p>

    <p class="formula">${escapeHtml(recovery.formula)}</p>
  `;
}
