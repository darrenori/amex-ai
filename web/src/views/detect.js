// Stage 1 — detection.
//
// The point this stage has to make is that nobody told the system. It swept the
// member's own flights against a flight-status API and found the cancellation
// itself, which is why the recovery can be waiting before the member thinks to
// look. The raw upstream payload is shown deliberately: a claim about being
// event-driven is worth less than the response that proves it.

import { escapeHtml, stampZoned } from '../format.js';
import { icons } from '../icons.js';

const modeTone = (mode) => ({ live: 'good', sandbox: 'warn', fixture: 'neutral' })[mode] ?? 'neutral';

function capabilityMode(capability, fallback = 'fixture') {
  if (typeof capability === 'string') return capability;
  if (capability && typeof capability.mode === 'string') return capability.mode;
  if (capability === true) return fallback;
  if (capability === false) return 'unavailable';
  return fallback;
}

function safeDocsUrl(raw) {
  try {
    const url = new URL(String(raw ?? ''));
    return url.protocol === 'https:' ? url.href : '';
  } catch {
    return '';
  }
}

function adapterLabel(value, fallback = 'adapter') {
  const label = String(value ?? fallback);
  return label.startsWith('mcp/') ? `adapter/${label.slice(4)}` : label;
}

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
  const mode = detection.connector.mode ?? 'fixture';
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
        <span class="chip chip-${modeTone(mode)}">
          <span class="dot"></span>${escapeHtml(mode)}
        </span>
      </div>

      <p class="muted stage-note">
        ${mode === 'live'
          ? 'A credential is configured, so these results came back from the real service just now.'
          : mode === 'sandbox'
            ? 'These results came from an authenticated provider sandbox; no production booking was touched.'
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

function runtimeMarkup(system = {}) {
  const ai = system.ai ?? system.ai_provider ?? null;
  const mcp = system.mcp ?? system.context_mcp ?? (ai?.tools || ai?.transport ? {
    status: ai.transport === 'unavailable' ? 'unavailable' : 'configured',
    transport: ai.transport,
    tools: ai.tools,
  } : null);
  if (!ai && !mcp) return '';

  const tools = Array.isArray(mcp?.tools) ? mcp.tools : (Array.isArray(mcp?.tool_names) ? mcp.tool_names : []);
  return `
    <div class="runtime-grid" aria-label="AI and MCP runtime status">
      ${mcp ? `
        <div class="runtime-card">
          <span class="runtime-label">TripShield Context MCP</span>
          <strong>${escapeHtml(mcp.status ?? 'configured')}</strong>
          <span>${escapeHtml(mcp.transport ?? 'in-process')} · ${tools.length} read-only tool${tools.length === 1 ? '' : 's'}</span>
          ${tools.length ? `<code>${tools.map((tool) => escapeHtml(tool?.name ?? tool)).join(' · ')}</code>` : ''}
        </div>` : ''}
      ${ai ? `
        <div class="runtime-card">
          <span class="runtime-label">Explanation provider</span>
          <strong>${escapeHtml(ai.provider ?? ai.selected_provider ?? ai.status ?? 'disabled')}</strong>
          <span>${escapeHtml(ai.model ?? 'No model selected')}</span>
          ${ai.status ? `<code>${escapeHtml(ai.status)}</code>` : ''}
        </div>` : ''}
    </div>`;
}

export function connectorMarkup(connectors = [], agents = [], system = {}) {
  const rows = connectors.map((spec) => {
    const readMode = capabilityMode(spec.capabilities?.read, spec.mode ?? 'fixture');
    const transactionMode = capabilityMode(spec.capabilities?.transaction, 'fixture');
    const docs = safeDocsUrl(spec.docs);
    const adapter = adapterLabel(spec.adapter ?? spec.server, spec.key ?? 'adapter');
    return `
    <tr>
      <td>
        <span class="mono">${escapeHtml(adapter)}</span>
        <span class="conn-upstream">${escapeHtml(spec.upstream)}</span>
      </td>
      <td>
        <ul class="tool-list">
          ${Object.entries(spec.tools ?? {}).map(([tool, endpoint]) => `
            <li><span class="mono">${escapeHtml(tool)}</span><code>${escapeHtml(endpoint)}</code></li>`).join('')}
        </ul>
      </td>
      <td>
        <span class="chip chip-${modeTone(readMode)}"><span class="dot"></span>${escapeHtml(readMode)}</span>
      </td>
      <td>
        <span class="chip chip-${modeTone(transactionMode)}"><span class="dot"></span>${escapeHtml(transactionMode)}</span>
      </td>
      <td>
        <span class="conn-availability">${escapeHtml(spec.availability ?? (spec.credential_present ? 'credential configured' : 'fallback ready'))}</span>
        ${docs ? `<a class="conn-docs" href="${escapeHtml(docs)}" target="_blank" rel="noopener noreferrer">docs</a>` : ''}
      </td>
    </tr>`;
  }).join('');

  return `
    <p class="inspect-divider">Connector adapters behind the agents</p>
    <p class="muted stage-note">
      External travel suppliers are called through direct REST adapters. Read paths can be live,
      sandbox or fixture-backed independently; every booking transaction remains a fixture so this
      demonstration cannot purchase, change or cancel real inventory.
    </p>
    ${runtimeMarkup(system)}
    <div class="table-scroll">
      <table class="conn-table">
        <thead><tr><th>Adapter</th><th>Operations</th><th>Read</th><th>Transaction</th><th>Availability</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="agent-row">
      ${agents.map((agent) => `
        <span class="agent-pill">
          <span class="agent-name">${escapeHtml(agent.name)}</span>
          <span class="agent-server mono">${escapeHtml(adapterLabel(
            agent.adapter ?? agent.server,
            agent.connector ?? 'adapter',
          ))}</span>
        </span>`).join('')}
    </div>`;
}
