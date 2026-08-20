// Stage 5 — execution and compensation.
//
// Two things this screen exists to get right.
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

import { escapeHtml, money, stampZoned } from '../format.js';
import { icons } from '../icons.js';

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
  approved: 'Approved — nothing committed yet',
  executing: 'Executing',
  complete: 'Recovery complete',
  cancelling: 'Cancelling',
  cancelled: 'Stopped — committed steps left in place',
  rolled_back: 'Rolled back',
  failed: 'Failed',
};

function stepRow(step, currency, isNext) {
  const tone = STEP_TONE[step.state] ?? 'neutral';
  const settled = ['done', 'compensated', 'skipped'].includes(step.state);
  const mark = settled ? icons.check : step.state === 'awaiting_approval' ? icons.alert : icons.clock;

  return `
    <li class="xstep xstep-${escapeHtml(step.state)}${isNext ? ' is-next' : ''}">
      <span class="xstep-rail" aria-hidden="true"></span>
      <span class="icon-badge ${tone}" aria-hidden="true">${mark}</span>
      <div class="xstep-body">
        <div class="xstep-top">
          <p class="xstep-title">${escapeHtml(step.title)}</p>
          <span class="chip chip-${tone}"><span class="dot"></span>${escapeHtml(STEP_LABEL[step.state] ?? step.state)}</span>
        </div>
        <p class="xstep-detail">${escapeHtml(step.detail)}</p>
        <p class="xstep-call mono">${escapeHtml(step.action)}</p>
        ${step.amount ? `
          <p class="xstep-amount ${step.amount > 0 ? 'neg' : 'pos'}">
            ${step.amount > 0 ? 'Charge' : 'Refund'} ${escapeHtml(money(Math.abs(step.amount), currency))}
          </p>` : ''}
        ${step.log.length ? `<ul class="xstep-log">${step.log.map((l) => `<li>${escapeHtml(l)}</li>`).join('')}</ul>` : ''}
        ${step.compensation && step.compensation.note ? `
          <p class="xstep-comp">${escapeHtml(step.compensation.note)}</p>` : ''}
      </div>
    </li>`;
}

export function runMarkup(run, currency) {
  const pct = Math.round(run.progress * 100);
  const next = run.steps.find((s) => ['pending', 'awaiting_approval'].includes(s.state));
  const awaiting = next && next.state === 'awaiting_approval';
  const finished = ['complete', 'cancelled', 'rolled_back', 'failed'].includes(run.state);
  const committed = run.steps.filter((s) => s.state === 'done').length;

  const charged = run.steps
    .filter((s) => s.state === 'done')
    .reduce((total, s) => total + (s.result?.charged ?? 0), 0);
  const refunded = run.steps
    .filter((s) => s.state === 'done')
    .reduce((total, s) => total + (s.result?.refunded ?? 0), 0);

  return `
    <div class="card">
      <div class="card-head">
        <div>
          <p class="eyebrow">Run ${escapeHtml(run.id)}</p>
          <h4 class="stage-title">${escapeHtml(run.plan.name)}</h4>
        </div>
        <span class="chip chip-${finished ? (run.state === 'complete' ? 'good' : 'neutral') : 'accent'}">
          <span class="dot"></span>${escapeHtml(RUN_LABEL[run.state] ?? run.state)}
        </span>
      </div>

      <div class="xprogress">
        <div class="xprogress-track" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"
             aria-label="Recovery progress">
          <div class="xprogress-fill" style="width:${pct}%"></div>
        </div>
        <p class="xprogress-label">
          ${pct}% · ${committed} of ${run.steps.length} transaction${run.steps.length === 1 ? '' : 's'} committed
          ${charged ? ` · ${escapeHtml(money(charged, currency))} charged` : ''}
          ${refunded ? ` · ${escapeHtml(money(refunded, currency))} refunded` : ''}
        </p>
      </div>

      <ol class="xsteps">
        ${run.steps.map((step) => stepRow(step, currency, step === next)).join('')}
      </ol>

      <div class="xactions">
        ${finished ? '' : `
          <button class="btn btn-primary" type="button" id="advanceRun">
            ${awaiting
              ? `Authorise ${escapeHtml(money(Math.abs(next.amount), currency))} and commit`
              : `Run next step${next ? ` — ${escapeHtml(next.title)}` : ''}`}
          </button>
          <button class="btn btn-quiet" type="button" id="stopRun">${icons.stop} Stop here</button>
          <button class="btn btn-quiet" type="button" id="undoRun">${icons.undo} Undo everything</button>`}
      </div>

      ${finished ? `
        <div class="xdone">
          <span class="icon-badge ${run.state === 'complete' ? 'good' : 'neutral'}" aria-hidden="true">${icons.check}</span>
          <div>
            <strong>${escapeHtml(RUN_LABEL[run.state] ?? run.state)}</strong>
            <p>${escapeHtml(run.log[run.log.length - 1] ?? '')}</p>
          </div>
        </div>` : ''}

      <details class="raw">
        <summary>Transaction log</summary>
        <ul class="run-log">${run.log.map((l) => `<li>${escapeHtml(l)}</li>`).join('')}</ul>
      </details>
    </div>`;
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
