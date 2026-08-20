// Stage 1 — detection.
//
// The point this stage has to make is that nobody told the system. It swept the
// member's own flights against a flight-status API and found the cancellation
// itself, which is why the recovery can be waiting before the member thinks to
// look. The raw upstream payload is shown deliberately: a claim about being
// event-driven is worth less than the response that proves it.

import { escapeHtml, stampZoned } from '../format.js';
import { icons } from '../icons.js';

function checkRow(check) {
  const tone = check.disruptive ? 'critical' : 'good';
  const label = check.disruptive ? check.status : `${check.status} · on schedule`;
  return `
    <li class="sweep-row${check.disruptive ? ' is-hit' : ''}">
      <span class="icon-badge ${tone}" aria-hidden="true">${check.disruptive ? icons.alert : icons.check}</span>
      <span class="sweep-main">
        <span class="sweep-flight">${escapeHtml(check.flight_number)}</span>
        <code class="sweep-endpoint">${escapeHtml(check.endpoint)}</code>
      </span>
      <span class="chip chip-${tone}"><span class="dot"></span>${escapeHtml(label)}</span>
    </li>`;
}

export function detectMarkup(detection, connectorSpec) {
  const live = detection.connector.mode === 'live';
  const disruption = detection.disruptions[0] ?? null;
  const hit = detection.checks.find((c) => c.disruptive) ?? null;

  return `
    <div class="section-head">
      <h3><span class="section-index">1.</span> Detection</h3>
      <p>
        Every upcoming flight on the trip is swept against a flight-status API. In production this
        runs on a schedule and on the carrier's alert webhook, so the recovery is ready before the
        member has looked at their phone. This button runs the same code path on demand.
      </p>
    </div>

    <div class="card">
      <div class="card-head">
        <div>
          <p class="eyebrow">Upstream</p>
          <h4 class="stage-title">${escapeHtml(detection.connector.upstream)}</h4>
        </div>
        <span class="chip chip-${live ? 'good' : 'neutral'}">
          <span class="dot"></span>${live ? 'Live' : 'Fixture'}
        </span>
      </div>

      <p class="muted stage-note">
        ${live
          ? 'A credential is configured, so these results came back from the real service just now.'
          : `Reading a flight's status is free and read-only, so this connector runs live as soon as
             <code>${escapeHtml(detection.connector.auth_env)}</code> is set. Without it the sweep
             replays a recorded response in the same shape.`}
        The status vocabulary below is ${escapeHtml(detection.connector.upstream)}'s own.
      </p>

      <ul class="sweep-list">${detection.checks.map(checkRow).join('')}</ul>

      ${disruption ? `
        <div class="alert alert-tight" role="status">
          <span class="icon-badge critical" aria-hidden="true">${icons.alert}</span>
          <div>
            <strong>${escapeHtml(disruption.headline)}</strong>
            <p>${escapeHtml(disruption.reason)} Detected ${escapeHtml(stampZoned(disruption.detected_at))},
               without the member reporting anything.</p>
          </div>
        </div>` : `
        <p class="muted stage-note">No disruption on any flight for this trip.</p>`}

      ${hit ? `
        <details class="raw">
          <summary>Upstream response for ${escapeHtml(hit.flight_number)}</summary>
          <pre><code>${escapeHtml(JSON.stringify(hit.raw, null, 2))}</code></pre>
        </details>` : ''}
    </div>`;
}

export function connectorMarkup(connectors, agents) {
  const rows = connectors.map((spec) => `
    <tr>
      <td>
        <span class="mono">${escapeHtml(spec.server)}</span>
        <span class="conn-upstream">${escapeHtml(spec.upstream)}</span>
      </td>
      <td>
        <ul class="tool-list">
          ${Object.entries(spec.tools).map(([tool, endpoint]) => `
            <li><span class="mono">${escapeHtml(tool)}</span><code>${escapeHtml(endpoint)}</code></li>`).join('')}
        </ul>
      </td>
      <td>
        <span class="chip chip-${spec.mode === 'live' ? 'good' : 'neutral'}"><span class="dot"></span>${escapeHtml(spec.mode)}</span>
        <a class="conn-docs" href="${escapeHtml(spec.docs)}" target="_blank" rel="noopener noreferrer">docs</a>
      </td>
    </tr>`).join('');

  return `
    <p class="inspect-divider">MCP servers behind the agents</p>
    <p class="muted stage-note">
      Each agent gets one standardized interface to one external capability. Every tool name below
      maps to a real endpoint on a real product; only the booking transactions run against fixtures,
      because a demonstration must not transact against live inventory.
    </p>
    <div class="table-scroll">
      <table class="conn-table">
        <thead><tr><th>Server</th><th>Tools</th><th>Mode</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="agent-row">
      ${agents.map((agent) => `
        <span class="agent-pill">
          <span class="agent-name">${escapeHtml(agent.name)}</span>
          <span class="agent-server mono">${escapeHtml(agent.server)}</span>
        </span>`).join('')}
    </div>`;
}
