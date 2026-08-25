// Stage 3 — the orchestrator's working.
//
// Not traveler-facing. This is the panel that answers "why should I believe the
// recommendation", by showing the tasks that were created, the constraints put
// on each one, which agent took it, what it ruled out and why.
//
// A rejected option with its reason is more convincing than a shortlist. The
// ruled-out lines are the ones worth reading.

import { escapeHtml, minutesLabel, money, signedMoney, stamp } from '../format.js';
import { icons } from '../icons.js';

const CONSTRAINT_LABEL = {
  priority: 'priority',
  not_before: 'not before',
  arrive_before: 'arrive before',
  arrival: 'arrival',
  arrival_buffer_minutes: 'buffer',
  latest_check_in: 'desk cut-off',
  must_end_before: 'must end before',
  in_city_until: 'in city until',
  replacement_departure: 'replacement departs',
  disrupted_at: 'disrupted at',
  max_extra_cost: 'cost ceiling',
  cabin: 'cabin',
};

const SKIP = new Set(['max_options', 'arrive_before_binding']);

function constraintValue(key, value) {
  if (value === null || value === undefined) return null;
  if (key.endsWith('_minutes')) return minutesLabel(value);
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value)) return stamp(value);
  return String(value);
}

function taskCard(task, optionsById) {
  const constraints = Object.entries(task.constraints)
    .filter(([key, value]) => !SKIP.has(key) && value !== null && value !== undefined)
    .map(([key, value]) => {
      const rendered = constraintValue(key, value);
      if (rendered === null) return '';
      return `<li><span>${escapeHtml(CONSTRAINT_LABEL[key] ?? key)}</span><code>${escapeHtml(rendered)}</code></li>`;
    })
    .join('');

  const kept = task.option_ids
    .map((id) => optionsById[id])
    .filter(Boolean)
    .map((option) => `
      <li class="trace-kept">
        <span class="icon-badge good" aria-hidden="true">${icons.check}</span>
        <span>
          <span class="trace-opt">${escapeHtml(option.title)}</span>
          <span class="trace-meta" title="${escapeHtml(option.tool_endpoint ?? '')}">${escapeHtml(signedMoney(option.cost_delta))} · ${escapeHtml(option.tool_call)}</span>
        </span>
      </li>`)
    .join('');

  const rejected = task.log
    .filter((line) => line.startsWith('ruled out'))
    .map((line) => {
      const [, id, reason] = /^ruled out ([^:]+): (.*)$/.exec(line) ?? [];
      const option = optionsById[id];
      return `
        <li class="trace-cut">
          <span class="trace-opt">${escapeHtml(option ? option.title : id)}</span>
          <span class="trace-meta">${escapeHtml(reason ?? line)}</span>
        </li>`;
    })
    .join('');

  const search = task.log.find((line) => line.includes('→')) ?? '';
  const failed = task.state === 'failed';

  return `
    <article class="trace-card${failed ? ' is-failed' : ''}">
      <div class="trace-head">
        <span class="trace-agent">${escapeHtml(task.agent)}</span>
        <span class="chip chip-${failed ? 'critical' : 'neutral'}"><span class="dot"></span>${escapeHtml(task.state)}</span>
      </div>
      <p class="trace-objective">${escapeHtml(task.objective)}</p>
      ${search ? `<code class="trace-call">${escapeHtml(search)}</code>` : ''}
      ${constraints ? `<ul class="trace-constraints">${constraints}</ul>` : ''}
      ${kept ? `<ul class="trace-options">${kept}</ul>` : ''}
      ${rejected ? `
        <details class="trace-rejects">
          <summary>Ruled out (${rejected.match(/<li/g)?.length ?? 0})</summary>
          <ul>${rejected}</ul>
        </details>` : ''}
    </article>`;
}

// Why the model stage did not run. "Safe fallback used" on its own tells a
// reviewer nothing actionable: a missing key, an exhausted balance and a
// malformed request need three different responses.
const AI_FAILURE_REASON = {
  missing_api_key: 'No API key is configured, so the deterministic specialist ran instead.',
  insufficient_quota: 'The AI account is out of credit, so the deterministic specialist ran instead.',
  rate_limit: 'The provider was rate-limiting, so the deterministic specialist ran instead.',
  authentication: 'The API key was rejected, so the deterministic specialist ran instead.',
  timeout: 'The model did not answer in time, so the deterministic specialist ran instead.',
  output_truncated: 'The model ran out of room mid-answer, so the deterministic specialist ran instead.',
  invalid_model_output: 'The model broke its output contract, so its answer was discarded.',
  recommendation_mismatch: 'The model tried to change the winner, so its answer was discarded.',
  provider_sdk_unavailable: 'The provider SDK is not installed, so the deterministic specialist ran instead.',
  mcp_sdk_unavailable: 'The MCP SDK is not installed, so the deterministic specialist ran instead.',
};

function agentRunCard(run) {
  const generated = run.status === 'generated';
  const neutral = run.status === 'not_requested';
  const failed = !generated && !neutral;
  const tone = generated ? 'good' : neutral ? 'neutral' : 'critical';
  const label = generated ? 'AI completed' : neutral ? 'No affected tasks' : 'Safe fallback used';
  const tools = Array.isArray(run.tools_used) ? run.tools_used : [];
  const assessments = Array.isArray(run.assessments) ? run.assessments : [];
  const recommendationRun = run.role === 'Recommendation AI';
  const scope = recommendationRun
    ? `${run.eligible_plan_ids?.length ?? 0} eligible plan(s)`
    : `${run.task_ids?.length ?? 0} task(s) · ${assessments.length} validated assessment(s)`;
  const metadata = [
    run.provider,
    run.model,
    Number.isFinite(Number(run.latency_ms)) ? `${Math.round(Number(run.latency_ms))} ms` : '',
  ].filter(Boolean).join(' · ');

  return `
    <article class="trace-card${failed ? ' is-failed' : ''}">
      <div class="trace-head">
        <span class="trace-agent">${escapeHtml(run.role ?? 'Specialist AI')}</span>
        <span class="chip chip-${tone}"><span class="dot"></span>${escapeHtml(label)}</span>
      </div>
      ${metadata ? `<p class="trace-objective">${escapeHtml(metadata)}</p>` : ''}
      <p class="trace-meta">
        ${scope}
        ${tools.length ? ` · ${tools.length} read-only tool(s)` : ''}
      </p>
      ${assessments.length ? `
        <details class="trace-rejects">
          <summary>Agent findings (${assessments.length})</summary>
          <ul>${assessments.map((finding) => `
            <li class="trace-cut">
              <span class="trace-opt">${escapeHtml(finding.recommended_option_id)}</span>
              <span class="trace-meta">${escapeHtml(finding.rationale)}</span>
            </li>`).join('')}</ul>
        </details>` : ''}
      ${!generated && !neutral ? `<p class="trace-meta">${escapeHtml(
        AI_FAILURE_REASON[run.error_code] ?? 'The deterministic specialist handled this stage.'
      )}</p>` : ''}
    </article>`;
}

export function traceMarkup(planning) {
  const optionsById = Object.fromEntries(planning.options.map((o) => [o.id, o]));

  // The root task first, then one card per distinct downstream booking. The
  // orchestrator creates a fresh downstream task for *every* candidate flight,
  // so showing all 23 would be noise; the first per booking is representative
  // and the count says how many were really run.
  const root = planning.tasks[0];
  const seen = new Set([root?.booking_id]);
  const downstream = planning.tasks.filter((task) => {
    if (seen.has(task.booking_id)) return false;
    seen.add(task.booking_id);
    return true;
  });
  const agentRuns = Array.isArray(planning.agent_runs) ? planning.agent_runs : [];
  const generatedRuns = agentRuns.filter((run) => run.status === 'generated').length;

  return `
    ${agentRuns.length ? `
      <p class="muted stage-note">
        <strong>${generatedRuns} of ${agentRuns.length}</strong> AI workflow stages completed.
        Any unavailable agent was replaced independently by its deterministic safety fallback.
      </p>
      <div class="trace-grid agent-run-grid">
        ${agentRuns.map(agentRunCard).join('')}
      </div>
      <p class="inspect-divider">Validated recovery tasks and connector options</p>` : ''}
    <p class="muted stage-note">
      The cancellation produced <strong>${planning.tasks.length}</strong> recovery tasks across
      <strong>${planning.agents.length}</strong> specialized agents, returning
      <strong>${planning.options.length}</strong> options. Downstream tasks are re-created for each
      candidate flight, because a different arrival breaks different things — one card per booking is
      shown here.
    </p>
    <div class="trace-grid">
      ${root ? taskCard(root, optionsById) : ''}
      ${downstream.map((task) => taskCard(task, optionsById)).join('')}
    </div>`;
}
