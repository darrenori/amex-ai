// Stage 5 — execution and compensation.
//
// Three things this screen exists to get right.
//
// **Steps run one at a time.** Later bookings depend on earlier ones — the hotel
// amendment is only correct once the replacement flight is ticketed. Firing
// every transaction at once is faster and produces a wrong trip when the third
// one fails. Each step is committed and verified before the next unlocks, and
// each charge is authorised on its own rather than swept into one approval.
//
// **Cancel is two different words.** "Stop" means execute nothing further.
// "Undo" means run compensating transactions against what is already committed
// — and those are not guaranteed to work. An airline quotes a refund net of its
// fee; a step that *gave a booking up* cannot be reversed by a refund at all,
// only by buying it back at today's price. So the undo path always shows the
// quote, with its unrecoverable column, before it does anything.
//
// **The list must not grow under the cursor.** Committing a step adds log lines
// and a pair of Amex links to it. Rendered inline, that pushed the button that
// commits the *next* step further down the page every single time, so the
// member scrolled down, clicked, and scrolled down again — five times for five
// transactions. So exactly one step is expanded at a time, the live one; every
// other step collapses to its headline with its receipt one click away. The
// list therefore stays roughly the same height from first commit to last, the
// action bar sticks to the bottom of the viewport, and `mountRun` patches the
// run in place rather than re-rendering it, so the state change is something
// you watch happen rather than something that has already happened.

import { animate } from 'motion';

import { escapeHtml, money } from '../format.js';
import { icons } from '../icons.js';
import { partnerLinksMarkup } from './partners.js';

const STEP_TONE = {
  pending: 'neutral',
  awaiting_approval: 'warn',
  in_progress: 'accent',
  done: 'good',
  failed: 'critical',
  skipped: 'neutral',
  compensating: 'warn',
  compensated: 'accent',
  compensation_failed: 'critical',
};

const STEP_LABEL = {
  pending: 'Queued',
  awaiting_approval: 'Awaiting your authorisation',
  in_progress: 'In progress',
  done: 'Committed',
  failed: 'Failed',
  skipped: 'Skipped',
  compensating: 'Reversing',
  compensated: 'Reversed',
  compensation_failed: 'Could not be reversed',
};

const RUN_LABEL = {
  approved: 'Approved, nothing committed yet',
  executing: 'Executing',
  complete: 'Recovery complete',
  cancelling: 'Cancelling',
  cancelled: 'Stopped, committed steps left in place',
  rolled_back: 'Rolled back',
  failed: 'Failed',
};

// A step the run has finished with, one way or another: it carries a tick and
// its receipt, rather than a clock and a promise.
const SETTLED = new Set(['done', 'compensated', 'skipped']);

// A step the run actually transacted. Only these turn their length of rail
// brand blue — the spine reads as the progress bar at the top of the card laid
// on its side, filled exactly as far as money has actually moved. A skipped
// step was never run and a failed one did not land, so both stay grey and the
// blue stops honestly at the last thing that really happened.
const PASSED = new Set(['done', 'compensated']);

// The step the member is being asked about right now. It is the only one that
// renders expanded, because it is the only one they have to read.
const LIVE = new Set(['awaiting_approval', 'in_progress', 'compensating']);

const FINISHED_RUN = new Set(['complete', 'cancelled', 'rolled_back', 'failed']);

// Motion constants. One easing for everything that moves, so the console has a
// single physical character; durations scale with how far a thing travels.
const EASE = [0.22, 1, 0.36, 1];
const DUR = { pop: 0.34, wash: 0.9, enter: 0.34 };

const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// ---------------------------------------------------------------------------
// Markup
// ---------------------------------------------------------------------------

function isSettled(step) { return SETTLED.has(step.state); }
function isPassed(step) { return PASSED.has(step.state); }

/** Everything about a step that is worth reading twice, but not every time. */
function stepDetailMarkup(step, currency, links) {
  return `
    <p class="xstep-call mono">${escapeHtml(step.action)}</p>
    ${step.log.length ? `<ul class="xstep-log">${step.log.map((l) => `<li>${escapeHtml(l)}</li>`).join('')}</ul>` : ''}
    ${step.compensation && step.compensation.note
      ? `<p class="xstep-comp">${escapeHtml(step.compensation.note)}</p>` : ''}
    ${step.state === 'done' ? partnerLinksMarkup(links) : ''}`;
}

// Only worth a toggle if there is something behind it. A queued step's one
// extra line is the tool call it *will* make — an implementation detail the
// member has already seen on the live step, and not worth 28px of control on
// every row of the list.
function hasReceipt(step, links) {
  return Boolean(step.log.length || step.compensation?.note || (step.state === 'done' && links.length));
}

function toggleMarkup(step, panelId, passed) {
  return `
    <button class="xstep-toggle" type="button" aria-expanded="false" aria-controls="${panelId}">
      <span class="xstep-toggle-label">${passed ? 'Receipt' : 'Details'}</span>
      <span class="expand" aria-hidden="true">${icons.chevron}</span>
      <span class="sr-only"> for ${escapeHtml(step.title)}</span>
    </button>`;
}

function stepRow(step, currency, isNext, links = [], expanded = false) {
  const tone = STEP_TONE[step.state] ?? 'neutral';
  const settled = isSettled(step);
  const mark = settled ? icons.check : step.state === 'awaiting_approval' ? icons.alert : icons.clock;
  const panelId = `xmore-${escapeHtml(step.id)}`;
  const toggle = !expanded && hasReceipt(step, links);
  const passed = isPassed(step) ? ' is-passed' : '';
  // Joined with no whitespace between them, so a step with neither an amount
  // nor a receipt leaves the row genuinely `:empty` and it collapses away.
  const foot = [
    step.amount
      ? `<p class="xstep-amount ${step.amount > 0 ? 'neg' : 'pos'}">`
        + `${step.amount > 0 ? 'Charge' : 'Refund'} ${escapeHtml(money(Math.abs(step.amount), currency))}</p>`
      : '',
    toggle ? toggleMarkup(step, panelId, isPassed(step)) : '',
  ].join('');

  return `
    <li class="xstep xstep-${escapeHtml(step.state)}${isNext ? ' is-next' : ''}${settled ? ' is-settled' : ''}${passed}"
        data-step="${escapeHtml(step.id)}" data-state="${escapeHtml(step.state)}">
      <span class="xstep-rail" aria-hidden="true"><i class="xstep-rail-fill"></i></span>
      <span class="icon-badge ${tone}" aria-hidden="true">${mark}</span>
      <div class="xstep-body">
        <div class="xstep-top">
          <p class="xstep-title">${escapeHtml(step.title)}</p>
          <span class="chip chip-${tone}"><span class="dot"></span>${escapeHtml(STEP_LABEL[step.state] ?? step.state)}</span>
        </div>
        <p class="xstep-detail">${escapeHtml(step.detail)}</p>
        <div class="xstep-foot">${foot}</div>
        <div class="xstep-more${expanded ? ' is-open' : ''}" id="${panelId}">
          <div class="xstep-more-inner">${stepDetailMarkup(step, currency, links)}</div>
        </div>
      </div>
    </li>`;
}

function actionsMarkup(run, currency) {
  const next = run.steps.find((s) => ['pending', 'awaiting_approval'].includes(s.state));
  const awaiting = next && next.state === 'awaiting_approval';
  if (FINISHED_RUN.has(run.state)) return '';

  return `
    <button class="btn btn-primary" type="button" id="advanceRun">
      ${awaiting
        ? `Authorise ${escapeHtml(money(Math.abs(next.amount), currency))} and commit`
        : `Run next step${next ? `, ${escapeHtml(next.title)}` : ''}`}
    </button>
    <button class="btn btn-quiet" type="button" id="stopRun">${icons.stop} Stop here</button>
    <button class="btn btn-quiet" type="button" id="undoRun">${icons.undo} Undo everything</button>`;
}

function progressLabelMarkup(run, currency) {
  const pct = Math.round(run.progress * 100);
  const committed = run.steps.filter((s) => s.state === 'done').length;
  const charged = run.steps
    .filter((s) => s.state === 'done')
    .reduce((total, s) => total + (s.result?.charged ?? 0), 0);
  const refunded = run.steps
    .filter((s) => s.state === 'done')
    .reduce((total, s) => total + (s.result?.refunded ?? 0), 0);

  return `${pct}% · ${committed} of ${run.steps.length} transaction${run.steps.length === 1 ? '' : 's'} committed
    ${charged ? ` · ${escapeHtml(money(charged, currency))} charged` : ''}
    ${refunded ? ` · ${escapeHtml(money(refunded, currency))} refunded` : ''}`;
}

function doneMarkup(run) {
  if (!FINISHED_RUN.has(run.state)) return '';
  return `
    <div class="xdone">
      <span class="icon-badge ${run.state === 'complete' ? 'good' : 'neutral'}" aria-hidden="true">${icons.check}</span>
      <div>
        <strong>${escapeHtml(RUN_LABEL[run.state] ?? run.state)}</strong>
        <p>${escapeHtml(run.log[run.log.length - 1] ?? '')}</p>
      </div>
    </div>`;
}

export function runMarkup(run, currency, optionsById = {}) {
  const pct = Math.round(run.progress * 100);
  const next = run.steps.find((s) => ['pending', 'awaiting_approval'].includes(s.state));
  const finished = FINISHED_RUN.has(run.state);

  return `
    <div class="card xrun">
      <div class="card-head">
        <div>
          <p class="eyebrow">Run ${escapeHtml(run.id)}</p>
          <h4 class="stage-title">${escapeHtml(run.plan.name)}</h4>
        </div>
        <span class="chip chip-${finished ? (run.state === 'complete' ? 'good' : 'neutral') : 'accent'}" data-run-chip>
          <span class="dot"></span>${escapeHtml(RUN_LABEL[run.state] ?? run.state)}
        </span>
      </div>

      <div class="xprogress">
        <div class="xprogress-track" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"
             aria-label="Recovery progress">
          <div class="xprogress-fill" style="width:${pct}%"></div>
        </div>
        <p class="xprogress-label">${progressLabelMarkup(run, currency)}</p>
      </div>

      <ol class="xsteps">
        ${run.steps.map((step) => stepRow(
          step,
          currency,
          step === next,
          optionsById[step.option_id]?.links ?? [],
          LIVE.has(step.state) || step === next,
        )).join('')}
      </ol>

      <div class="xactions">${actionsMarkup(run, currency)}</div>

      <div data-run-done>${doneMarkup(run)}</div>

      <details class="raw">
        <summary>Transaction log</summary>
        <ul class="run-log">${run.log.map((l) => `<li>${escapeHtml(l)}</li>`).join('')}</ul>
      </details>
    </div>`;
}

// ---------------------------------------------------------------------------
// Collapse
// ---------------------------------------------------------------------------

// Open and shut is a *resting state*, so it is a class and a CSS transition
// rather than a scripted animation: `grid-template-rows: 0fr -> 1fr` needs no
// measurement, and — the reason it matters — an interrupted transition still
// leaves the panel at a height the layout agrees with. A JS height tween that
// is paused by a backgrounded tab strands the panel mid-fold instead, which is
// worse than not animating it at all. Motion is kept for the flourishes below,
// where a frozen animation costs nothing because the CSS resting state is
// already correct underneath it.
function setExpanded(panel, toggle, open) {
  // "Receipt" only where there is one to read. A skipped step has a log line
  // saying why it never ran, which is a detail, not a receipt.
  const passed = panel.closest('.xstep')?.classList.contains('is-passed');
  toggle?.setAttribute('aria-expanded', String(open));
  const label = toggle?.querySelector('.xstep-toggle-label');
  if (label) label.textContent = open ? 'Hide' : passed ? 'Receipt' : 'Details';
  panel.classList.toggle('is-open', open);
}

// ---------------------------------------------------------------------------
// Mount and patch
// ---------------------------------------------------------------------------

/**
 * Render the run into `host` and hand back an `update(run)` that patches it.
 *
 * Patching rather than re-rendering is what makes the transition legible: the
 * `<li>` for a step keeps its identity across a commit, so its rail can be
 * drawn in, its badge can pop, and its body can fold away — three things a
 * wholesale `innerHTML` swap can only teleport between.
 */
export function mountRun(host, { run, currency, optionsById = {} }) {
  host.innerHTML = runMarkup(run, currency, optionsById);
  let current = run;
  let options = optionsById;

  host.addEventListener('click', (event) => {
    const toggle = event.target.closest('.xstep-toggle');
    if (!toggle) return;
    const panel = host.querySelector(`#${CSS.escape(toggle.getAttribute('aria-controls'))}`);
    if (panel) setExpanded(panel, toggle, toggle.getAttribute('aria-expanded') !== 'true');
  });

  function update(nextRun, nextOptions = options) {
    const previous = new Map(current.steps.map((s) => [s.id, s.state]));
    current = nextRun;
    options = nextOptions;

    const next = nextRun.steps.find((s) => ['pending', 'awaiting_approval'].includes(s.state));
    const card = host.querySelector('.xrun');
    const animated = !reduced();
    const justSettled = [];
    let becameLive = null;

    for (const step of nextRun.steps) {
      const li = card.querySelector(`[data-step="${CSS.escape(step.id)}"]`);
      if (!li) continue;

      const was = previous.get(step.id);
      // A queued step becomes the live one without its own state changing —
      // the step in front of it committed. That still has to repaint, or the
      // list ends up with nothing marked live at all.
      const wasNext = li.classList.contains('is-next');
      const isNext = step === next;
      const isLive = LIVE.has(step.state) || isNext;

      li.className = `xstep xstep-${step.state}${isNext ? ' is-next' : ''}`
        + `${isSettled(step) ? ' is-settled' : ''}${isPassed(step) ? ' is-passed' : ''}`;
      li.dataset.state = step.state;

      if (isSettled(step) && !SETTLED.has(was)) justSettled.push(li);
      if (isLive && !(LIVE.has(was) || wasNext)) becameLive = li;
      if (was === step.state && isNext === wasNext) continue;

      // Badge, chip and the collapsible contents are all state-derived; swap
      // them wholesale, then let the fold settle to whatever the new state
      // says it should be.
      const panel = li.querySelector('.xstep-more');
      const badge = li.querySelector('.icon-badge');
      const tone = STEP_TONE[step.state] ?? 'neutral';
      badge.className = `icon-badge ${tone}`;
      badge.innerHTML = isSettled(step) ? icons.check
        : step.state === 'awaiting_approval' ? icons.alert : icons.clock;

      const chip = li.querySelector('.xstep-top .chip');
      chip.className = `chip chip-${tone}`;
      chip.innerHTML = `<span class="dot"></span>${escapeHtml(STEP_LABEL[step.state] ?? step.state)}`;

      if (panel) {
        const links = options[step.option_id]?.links ?? [];
        panel.querySelector('.xstep-more-inner').innerHTML = stepDetailMarkup(step, currency, links);

        // Settling a step closes it; unlocking one opens it. A step the member
        // opened themselves and that has not changed state is left alone.
        const open = isLive;
        setExpanded(panel, syncToggle(li, panel, step, links, open), open);
      }
    }

    // -- run-level chrome ---------------------------------------------------

    const pct = Math.round(nextRun.progress * 100);
    const track = card.querySelector('.xprogress-track');
    track.setAttribute('aria-valuenow', String(pct));
    card.querySelector('.xprogress-fill').style.width = `${pct}%`;
    card.querySelector('.xprogress-label').innerHTML = progressLabelMarkup(nextRun, currency);

    const finished = FINISHED_RUN.has(nextRun.state);
    const runChip = card.querySelector('[data-run-chip]');
    runChip.className = `chip chip-${finished ? (nextRun.state === 'complete' ? 'good' : 'neutral') : 'accent'}`;
    runChip.innerHTML = `<span class="dot"></span>${escapeHtml(RUN_LABEL[nextRun.state] ?? nextRun.state)}`;

    const actions = card.querySelector('.xactions');
    const hadFocus = actions.contains(document.activeElement);
    actions.innerHTML = actionsMarkup(nextRun, currency);
    // The commit button is replaced under the pointer every time. Without this,
    // a keyboard member is dumped back at the top of the document after every
    // single step — the exact scroll they were promised they would not need.
    if (hadFocus) actions.querySelector('#advanceRun')?.focus({ preventScroll: true });

    card.querySelector('[data-run-done]').innerHTML = doneMarkup(nextRun);
    card.querySelector('.run-log').innerHTML =
      nextRun.log.map((l) => `<li>${escapeHtml(l)}</li>`).join('');

    if (animated) playTransitions({ justSettled, becameLive });
    return { justSettled, becameLive };
  }

  return {
    update,
    /** The `<li>` the member is being asked about, for scroll and focus. */
    liveStep: () => host.querySelector('.xstep.is-next, .xstep-awaiting_approval, .xstep-in_progress'),
  };
}

/**
 * Add, remove or relabel a step's receipt toggle. A step gains one the moment
 * committing it produces something to show, and loses it while it is the live
 * step, which is already open.
 */
function syncToggle(li, panel, step, links, open) {
  let toggle = li.querySelector('.xstep-toggle');
  if (open || !hasReceipt(step, links)) {
    toggle?.remove();
    return null;
  }
  if (!toggle) {
    li.querySelector('.xstep-foot')
      .insertAdjacentHTML('beforeend', toggleMarkup(step, panel.id, isPassed(step)));
    toggle = li.querySelector('.xstep-toggle');
  }
  toggle.setAttribute('aria-expanded', 'false');
  toggle.querySelector('.xstep-toggle-label').textContent = isPassed(step) ? 'Receipt' : 'Details';
  return toggle;
}

// The flourishes. Every one of these starts and ends at the value the
// stylesheet already holds, so they are pure decoration: if one is interrupted
// the row still looks exactly the way the run's state says it should.
function playTransitions({ justSettled, becameLive }) {
  for (const li of justSettled) {
    const badge = li.querySelector('.icon-badge');
    if (badge) {
      animate(
        badge,
        { transform: ['scale(0.55)', 'scale(1.14)', 'scale(1)'] },
        { duration: DUR.pop, ease: EASE },
      );
    }
    // A blue wash across the row, so a commit halfway up the list is still
    // noticed when the member's eye is on the button at the bottom of it.
    animate(
      li,
      { backgroundColor: ['rgba(0, 111, 207, 0.12)', 'rgba(0, 111, 207, 0)'] },
      { duration: DUR.wash, ease: 'easeOut' },
    );
  }

  if (becameLive) {
    animate(
      becameLive.querySelector('.xstep-body'),
      { opacity: [0.4, 1], transform: ['translateY(6px)', 'translateY(0)'] },
      { duration: DUR.enter, ease: EASE },
    );
  }
}

export function rollbackDialogMarkup(quote, currency) {
  const lines = quote.committed.map((line) => {
    const q = line.quote;
    return `
      <li class="rb-line">
        <div class="rb-top">
          <span class="rb-title">${escapeHtml(line.title)}</span>
          <span class="${q.refund_amount ? 'pos' : 'neg'}">
            ${q.refund_amount ? `${escapeHtml(money(q.refund_amount, currency))} back` : 'Nothing back'}
          </span>
        </div>
        <p class="rb-note">${escapeHtml(q.note)}</p>
        <p class="rb-call mono">${escapeHtml(line.compensating_action)}</p>
      </li>`;
  }).join('');

  return `
    <p class="eyebrow">Before anything is reversed</p>
    <h2 id="modalTitle">Undo the committed steps</h2>
    <p class="modal-intro">
      ${quote.committed.length
        ? `Each supplier was asked what reversing its step would actually return. This is their
           answer, not an estimate.`
        : 'Nothing has been committed yet, so there is nothing to reverse.'}
    </p>

    ${lines ? `<ul class="rb-list">${lines}</ul>` : ''}

    <div class="modal-summary">
      <span>Comes back to the Card</span>
      <strong class="pos">${escapeHtml(money(quote.refundable_total, currency))}</strong>
    </div>
    <div class="modal-summary">
      <span>Cannot be recovered</span>
      <strong class="${quote.unrecoverable_total ? 'neg' : ''}">${escapeHtml(money(quote.unrecoverable_total, currency))}</strong>
    </div>

    ${quote.will_be_skipped.length ? `
      <p class="modal-intro">
        ${quote.will_be_skipped.length} queued step${quote.will_be_skipped.length === 1 ? '' : 's'}
        will be skipped and never run.
      </p>` : ''}

    <p class="${quote.fully_reversible ? 'viol-clear' : 'rb-warn'}">${escapeHtml(quote.note)}</p>

    <div class="modal-actions">
      <button class="btn btn-quiet" type="button" data-close-modal>Keep going</button>
      <button class="btn btn-primary" type="button" id="confirmRollback">Reverse what you can</button>
    </div>`;
}
