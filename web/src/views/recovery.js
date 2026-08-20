// The recovery console — the human-in-the-loop half of the orchestrator.
//
// Five stages, in the order the workflow actually runs:
//
//   1  Detect    sweep the member's flights against a flight-status API
//   2  Impact    reconstruct the dependency graph and propagate the breakage
//   3  Plan      compare whole candidate plans, ranked under a chosen weighting
//   4  Adjust    rearrange one by hand, revalidated server-side on every change
//   5  Execute   commit it a step at a time, with a real way back out
//
// Stages unlock in order and stay reachable once passed, so a reviewer can walk
// back to the graph while a run is in flight without losing it.

import { api } from '../api.js';
import { escapeHtml, money } from '../format.js';
import { icons } from '../icons.js';
import { closeModal, openModal } from '../components/modal.js';
import { connectorMarkup, detectMarkup } from './detect.js';
import { graphMarkup, impactMarkup } from './graph.js';
import { traceMarkup } from './trace.js';
import { historyMarkup, plansMarkup, weightingMarkup } from './plans.js';
import { renderEditor } from './editor.js';
import { rollbackDialogMarkup, runMarkup } from './execute.js';

const STAGES = [
  { id: 'detect', label: 'Detect', hint: 'Find the disruption' },
  { id: 'impact', label: 'Impact', hint: 'What it reaches' },
  { id: 'plan', label: 'Plan', hint: 'Compare recoveries' },
  { id: 'adjust', label: 'Adjust', hint: 'Rearrange by hand' },
  { id: 'execute', label: 'Execute', hint: 'Commit, or back out' },
];

export function renderRecovery(container, { profiles, currency, onBack, announce }) {
  const state = {
    stage: 'detect',
    reached: new Set(['detect']),
    detection: null,
    graph: null,
    connectors: null,
    planning: null,
    ranking: null,
    priority: 'inferred',
    profileId: profiles[0]?.id ?? 'time',
    editing: null,
    run: null,
    busy: false,
  };

  container.innerHTML = `
    <div class="page-head">
      <button class="btn btn-quiet" id="backToAccount" type="button">${icons.arrowLeft} Back to account</button>
      <p class="eyebrow" style="margin-top:24px">Travel Recovery Orchestrator</p>
      <h2>One cancellation, priced against the whole journey.</h2>
      <p>
        Every booking on this trip went on the same Card, so the platform can reconstruct the entire
        itinerary and the dependencies between its parts. That is what makes it possible to answer
        what the cancellation actually costs, rather than what the new fare costs.
      </p>
    </div>

    <nav class="stage-rail" id="stageRail" aria-label="Recovery stages"></nav>
    <div id="stageBody" class="stage-body"></div>
  `;

  const rail = container.querySelector('#stageRail');
  const body = container.querySelector('#stageBody');

  // -- rail --------------------------------------------------------------

  function paintRail() {
    const activeIndex = STAGES.findIndex((s) => s.id === state.stage);
    rail.innerHTML = STAGES.map((stage, index) => {
      const reached = state.reached.has(stage.id);
      const active = stage.id === state.stage;
      return `
        <button class="stage-tab${active ? ' is-active' : ''}${index < activeIndex ? ' is-done' : ''}"
                type="button" data-stage="${stage.id}" ${reached ? '' : 'disabled'}
                aria-current="${active ? 'step' : 'false'}">
          <span class="stage-num" aria-hidden="true">${reached && index < activeIndex ? icons.check : index + 1}</span>
          <span class="stage-copy">
            <span class="stage-name">${stage.label}</span>
            <span class="stage-hint">${stage.hint}</span>
          </span>
        </button>`;
    }).join('');
  }

  function go(stage) {
    state.stage = stage;
    state.reached.add(stage);
    paintRail();
    paint();
    body.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }

  rail.addEventListener('click', (event) => {
    const tab = event.target.closest('[data-stage]');
    if (tab && !tab.disabled) go(tab.dataset.stage);
  });

  // -- loading -----------------------------------------------------------

  function busy(message) {
    body.innerHTML = `
      <div class="card stage-loading" role="status">
        <span class="spinner" aria-hidden="true"></span>
        <p>${escapeHtml(message)}</p>
      </div>`;
  }

  function failure(error) {
    body.innerHTML = `
      <div class="alert" role="alert">
        <span class="icon-badge critical" aria-hidden="true">${icons.alert}</span>
        <div><strong>Something went wrong</strong><p>${escapeHtml(error.message)}</p></div>
      </div>`;
  }

  // -- stage 1 -----------------------------------------------------------

  async function loadDetection() {
    busy('Sweeping every upcoming flight on this trip…');
    try {
      const [detection, connectors] = await Promise.all([api.detect(), api.connectors()]);
      state.detection = detection;
      state.connectors = connectors;
      paint();
      const found = detection.disruptions[0];
      announce(found
        ? `${found.headline}. ${found.reason}`
        : 'No disruption found on any flight for this trip.');
    } catch (error) {
      failure(error);
    }
  }

  function paintDetect() {
    if (!state.detection) return loadDetection();
    body.innerHTML = `
      ${detectMarkup(state.detection, state.connectors)}
      <div class="card">${connectorMarkup(state.connectors.connectors, state.connectors.agents)}</div>
      <div class="stage-next">
        <button class="btn btn-primary" type="button" id="toImpact">
          See what it reaches ${icons.arrowRight}
        </button>
      </div>`;
    body.querySelector('#toImpact').addEventListener('click', () => go('impact'));
  }

  // -- stage 2 -----------------------------------------------------------

  async function loadGraph() {
    busy('Reconstructing the itinerary and propagating the disruption…');
    try {
      state.graph = await api.graph();
      paint();
      const affected = state.graph.assessment?.affected.length ?? 0;
      announce(`${affected} bookings are reached by the cancellation.`);
    } catch (error) {
      failure(error);
    }
  }

  function paintImpact() {
    if (!state.graph) return loadGraph();
    const assessment = state.graph.assessment;
    body.innerHTML = `
      <div class="section-head">
        <h3><span class="section-index">2.</span> Dependency graph and impact</h3>
        <p>
          Not every relationship is a hard dependency, which is why this is a graph and not a list.
          A violated <strong>hard</strong> edge invalidates the booking it points at; a violated
          <strong>soft</strong> edge only degrades it. Most of what follows is arithmetic —
          <em>arrival + buffer &gt; start</em> — and needs no model at all.
        </p>
      </div>
      <div class="card">${graphMarkup(state.graph, assessment, currency)}</div>
      <div class="card">${impactMarkup(state.graph, assessment, currency)}</div>
      <div class="stage-next">
        <button class="btn btn-primary" type="button" id="toPlan">
          Generate recovery plans ${icons.arrowRight}
        </button>
      </div>`;
    body.querySelector('#toPlan').addEventListener('click', () => go('plan'));
  }

  // -- stage 3 -----------------------------------------------------------

  async function loadPlanning() {
    busy('Creating recovery tasks and delegating them to the agents…');
    try {
      state.planning = await api.plan(state.priority, state.profileId);
      state.ranking = state.planning.ranking;
      paint();
      announce(state.ranking.explanation);
    } catch (error) {
      failure(error);
    }
  }

  async function reRank() {
    try {
      const result = await api.rank(state.priority, state.profileId);
      state.planning = { ...state.planning, plans: result.plans };
      state.ranking = result.ranking;
      paint();
      announce(state.ranking.explanation);
    } catch (error) {
      announce(error.message);
    }
  }

  function paintPlan() {
    if (!state.planning) return loadPlanning();

    const optionsById = Object.fromEntries(state.planning.options.map((o) => [o.id, o]));
    const profile = profiles.find((p) => p.id === state.profileId);

    body.innerHTML = `
      <div class="section-head">
        <h3><span class="section-index">3.</span> Candidate recovery plans</h3>
        <p>
          Each card is a complete answer, assembled end to end for one objective — not a shortlist of
          the best flight next to the best hotel change, which would be a plan nobody chose and totals
          that describe no real trip.
        </p>
      </div>

      <div class="card">
        ${plansMarkup(state.planning.plans, state.ranking, currency, optionsById)}
      </div>

      <div class="card">
        <p class="inspect-divider">How the ranking was reached</p>
        ${weightingMarkup(state.ranking, profiles, state.profileId, state.priority)}
        ${state.priority === 'inferred' ? historyMarkup(profile) : ''}
      </div>

      <div class="card">
        <p class="inspect-divider">Behind the scenes — tasks, agents and what they ruled out</p>
        ${traceMarkup(state.planning)}
      </div>`;

    body.addEventListener('click', onPlanClick);
  }

  function onPlanClick(event) {
    const priority = event.target.closest('[data-priority]');
    if (priority) {
      state.priority = priority.dataset.priority;
      reRank();
      return;
    }
    const profile = event.target.closest('[data-profile]');
    if (profile && !profile.disabled) {
      state.profileId = profile.dataset.profile;
      reRank();
      return;
    }
    const edit = event.target.closest('[data-edit-plan]');
    if (edit) {
      state.editing = state.planning.plans.find((p) => p.id === edit.dataset.editPlan);
      go('adjust');
      return;
    }
    const choose = event.target.closest('[data-choose-plan]');
    if (choose) {
      const plan = state.planning.plans.find((p) => p.id === choose.dataset.choosePlan);
      confirmApproval(plan, choose);
    }
  }

  // -- stage 4 -----------------------------------------------------------

  function paintAdjust() {
    if (!state.planning) return loadPlanning();
    const base = state.editing
      ?? state.planning.plans.find((p) => p.id === state.ranking.recommended_plan_id)
      ?? state.planning.plans[0];
    state.editing = base;

    body.innerHTML = `
      <div class="section-head">
        <h3><span class="section-index">4.</span> Adjust the plan</h3>
        <p>
          Drag an alternative into the booking it belongs to, or press <em>Use</em>. Every change is
          sent to the backend, which re-propagates the graph and returns the verdict — the browser
          proposes, it never decides whether the result works.
        </p>
      </div>
      <div id="editorHost"></div>`;

    renderEditor(body.querySelector('#editorHost'), {
      planning: state.planning,
      basePlan: base,
      currency,
      priority: state.priority,
      profileId: state.profileId,
      announce,
      onApprove: (plan) => confirmApproval(plan, body.querySelector('#approveEdited')),
    });
  }

  // -- approval ----------------------------------------------------------

  function confirmApproval(plan, trigger) {
    if (!plan) return;
    const m = plan.metrics;

    const modal = openModal(trigger, `
      <p class="eyebrow">Review before anything is transacted</p>
      <h2 id="modalTitle">Approve ${escapeHtml(plan.name)}</h2>
      <p class="modal-intro">
        Approving freezes this plan as a snapshot and queues its transactions in dependency order.
        Nothing is charged until you authorise each step individually.
      </p>
      <ol class="approval-list">
        <li>Apply a whole-trip impact of <strong>${escapeHtml(money(m.cost_delta, currency))}</strong>${m.cost_delta < 0 ? ' in the member’s favour' : ''}.</li>
        <li>Re-transact <strong>${m.bookings_changed}</strong> booking${m.bookings_changed === 1 ? '' : 's'} across ${new Set(Object.keys(plan.selections)).size} supplier relationships.</li>
        <li>Give up <strong>${escapeHtml(money(m.forfeited, currency))}</strong> of non-refundable spend${m.experience_lost ? ` and ${escapeHtml(money(m.experience_lost, currency))} of booked experience` : ''}.</li>
        <li>Expect <strong>${escapeHtml(money(m.refund_expected, currency))}</strong> back to the Card.</li>
      </ol>
      <div class="modal-summary"><span>Arrival</span><strong>${escapeHtml(m.arrival ? m.arrival.slice(0, 16).replace('T', ' ') : '—')}</strong></div>
      <p class="subdued" style="margin-top:16px">
        Illustrative simulation. No real purchase, cancellation, refund or claim will occur.
      </p>
      <div class="modal-actions">
        <button class="btn btn-quiet" type="button" data-close-modal>Go back</button>
        <button class="btn btn-primary" type="button" id="doApprove">Approve and queue</button>
      </div>
    `);

    modal.querySelector('#doApprove').addEventListener('click', async () => {
      try {
        const result = await api.approve(plan.id);
        state.run = result.run;
        closeModal(false);
        go('execute');
        announce(`${plan.name} approved. ${result.run.steps.length} transactions queued. Nothing is charged yet.`);
      } catch (error) {
        announce(error.message);
        closeModal();
      }
    });
  }

  // -- stage 5 -----------------------------------------------------------

  function paintExecute() {
    if (!state.run) {
      body.innerHTML = `
        <div class="card stage-empty">
          <p>No plan has been approved yet.</p>
          <button class="btn btn-primary" type="button" id="backToPlans">Choose a plan</button>
        </div>`;
      body.querySelector('#backToPlans').addEventListener('click', () => go('plan'));
      return;
    }

    body.innerHTML = `
      <div class="section-head">
        <h3><span class="section-index">5.</span> Execution</h3>
        <p>
          One transaction at a time, in dependency order, each verified before the next unlocks.
          Charges are authorised individually. <em>Stop here</em> leaves committed steps in place;
          <em>Undo everything</em> asks each supplier what it would actually give back first.
        </p>
      </div>
      ${runMarkup(state.run, currency)}`;

    const advance = body.querySelector('#advanceRun');
    if (advance) advance.addEventListener('click', onAdvance);
    body.querySelector('#stopRun')?.addEventListener('click', () => onCancel(false));
    body.querySelector('#undoRun')?.addEventListener('click', () => onCancel(true));
  }

  async function onAdvance(event) {
    if (state.busy) return;
    state.busy = true;
    const button = event.currentTarget;
    button.disabled = true;

    try {
      const next = state.run.steps.find((s) => ['pending', 'awaiting_approval'].includes(s.state));
      const authorising = next?.state === 'awaiting_approval';
      const result = await api.advance(state.run.id, authorising);
      state.run = result.run;
      paint();

      const step = result.result.step;
      if (result.result.reason === 'awaiting_payment') {
        announce(`${step.title} needs your authorisation for ${money(Math.abs(step.amount), currency)} before it is committed.`);
      } else if (step) {
        announce(`${step.title} committed. ${Math.round(state.run.progress * 100)} percent complete.`);
      } else {
        announce('Recovery complete. Every step settled.');
      }
    } catch (error) {
      announce(error.message);
      button.disabled = false;
    } finally {
      state.busy = false;
    }
  }

  async function onCancel(rollback) {
    if (!rollback) {
      try {
        const result = await api.cancelRun(state.run.id, false);
        state.run = result.run;
        paint();
        announce('Recovery stopped. Steps already committed are left in place — nothing was reversed.');
      } catch (error) {
        announce(error.message);
      }
      return;
    }

    let quote;
    try {
      quote = (await api.rollbackQuote(state.run.id)).quote;
    } catch (error) {
      announce(error.message);
      return;
    }

    const modal = openModal(document.querySelector('#undoRun'), rollbackDialogMarkup(quote, currency));
    modal.querySelector('#confirmRollback').addEventListener('click', async () => {
      try {
        const result = await api.cancelRun(state.run.id, true);
        state.run = result.run;
        closeModal(false);
        paint();
        announce(
          `Rollback complete. ${money(result.refunded, currency)} refunded` +
          (result.unrecoverable ? `, ${money(result.unrecoverable, currency)} could not be recovered.` : ' in full.'),
        );
      } catch (error) {
        announce(error.message);
        closeModal();
      }
    });
  }

  // -- paint -------------------------------------------------------------

  function paint() {
    body.removeEventListener('click', onPlanClick);
    if (state.stage === 'detect') return paintDetect();
    if (state.stage === 'impact') return paintImpact();
    if (state.stage === 'plan') return paintPlan();
    if (state.stage === 'adjust') return paintAdjust();
    if (state.stage === 'execute') return paintExecute();
  }

  container.querySelector('#backToAccount').addEventListener('click', onBack);

  paintRail();
  paint();

  return () => {
    body.removeEventListener('click', onPlanClick);
  };
}
