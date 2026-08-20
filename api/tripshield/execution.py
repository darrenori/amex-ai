"""The execution engine — a saga over the approved plan.

Two rules shape everything here.

**Sequential, not parallel.** Later bookings depend on earlier ones: the hotel
amendment is only correct once the replacement flight is ticketed, and the
transfer only once the hotel is fixed. Firing all four transactions at once is
faster and produces a wrong trip when the third one fails. Steps run in the
itinerary's own dependency order, each verified before the next is unlocked.

**Cancel is two different words.** "Stop" means: execute nothing further.
"Undo" means: run compensating transactions for everything already committed —
and those are not guaranteed to succeed. Duffel will quote a refund net of an
airline fee. A same-day restaurant cancellation returns nothing at all. So the
engine always *asks* before it compensates (``rollback_quote``), shows the
member exactly what will and will not come back, and only then acts.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from . import connectors
from .catalog import JST
from .domain import (
    ExecutionRun,
    ExecutionStep,
    Option,
    RecoveryPlan,
    RunState,
    StepState,
    money,
)
from .graph import Itinerary


def _endpoint(spec: connectors.ConnectorSpec, action: str) -> str:
    """``lodging.book`` -> the real path the connector maps ``book`` onto."""
    tool = action.split(".")[-1]
    return spec.tools.get(tool, action)


def build_run(
    itinerary: Itinerary,
    plan: RecoveryPlan,
    catalogue: Dict[str, Option],
) -> ExecutionRun:
    """Freeze the approved plan into an ordered, executable snapshot.

    The snapshot matters. Re-planning can continue while a run is in flight, and
    compensation has to undo what was actually committed rather than whatever is
    on screen when the member changes their mind.
    """
    order = {bid: i for i, bid in enumerate(itinerary.topological_order())}

    chosen = [
        (booking_id, catalogue[option_id])
        for booking_id, option_id in plan.selections.items()
        if option_id in catalogue
    ]
    # A no-op selection ("keep the booking") is not a transaction. It stays out
    # of the run so the progress bar counts real work.
    chosen = [(bid, o) for bid, o in chosen if o.changes_booking or o.optional]
    chosen.sort(key=lambda row: order.get(row[0], len(order)))

    steps: List[ExecutionStep] = []
    for index, (booking_id, option) in enumerate(chosen):
        item = connectors.INVENTORY_BY_ID.get(option.id)
        spec = connectors.SPECS[option.connector]

        if option.drops_booking:
            # Giving a booking up runs the *cancel* endpoint on the way in, and
            # reversing it means buying it back — the two are not symmetric, and
            # pretending otherwise is how a rollback quietly fails.
            action = item.compensating_action if item else f"{option.connector}.cancel"
            compensating = item.action if item else f"{option.connector}.book"
        else:
            action = item.action if item else f"{option.connector}.book"
            compensating = item.compensating_action if item else f"{option.connector}.cancel"

        requires_payment = bool(item.requires_payment) and option.cost_delta > 0 if item else option.cost_delta > 0

        steps.append(ExecutionStep(
            id=f"step_{index + 1:02d}",
            index=index,
            booking_id=booking_id,
            option_id=option.id,
            connector=option.connector,
            agent=option.agent,
            title=option.title,
            detail=option.detail,
            action=f"{spec.server} · {_endpoint(spec, action)}",
            compensating_action=f"{spec.server} · {_endpoint(spec, compensating)}",
            amount=option.cost_delta,
            requires_payment=requires_payment,
        ))

    run = ExecutionRun(
        id=f"run_{uuid.uuid4().hex[:10]}",
        plan_snapshot=plan.public(),
        steps=steps,
        state=RunState.APPROVED,
        approved_at=datetime.now(JST),
    )
    run.log.append(
        f"Plan {plan.id} v{plan.version} approved — {len(steps)} transaction"
        f"{'' if len(steps) == 1 else 's'} queued in dependency order."
    )
    return run


# ---------------------------------------------------------------------------
# Forward execution
# ---------------------------------------------------------------------------

def _next_step(run: ExecutionRun) -> Optional[ExecutionStep]:
    for step in run.steps:
        if step.state in (StepState.PENDING, StepState.AWAITING_APPROVAL):
            return step
    return None


def advance(run: ExecutionRun, *, approve_payment: bool = False) -> Dict[str, Any]:
    """Move the run forward by exactly one transition.

    A step that costs money stops at ``AWAITING_APPROVAL`` and does not move
    again until the caller comes back with ``approve_payment=True``. The member
    authorises each charge, not the plan as a whole.
    """
    if run.state in (RunState.CANCELLED, RunState.ROLLED_BACK, RunState.COMPLETE):
        return {"changed": False, "reason": f"Run is already {run.state.value}."}

    step = _next_step(run)
    if step is None:
        run.state = RunState.COMPLETE
        run.log.append("All steps settled. Recovery complete.")
        return {"changed": True, "reason": "complete"}

    run.state = RunState.EXECUTING

    if step.state is StepState.PENDING and step.requires_payment and not approve_payment:
        step.state = StepState.AWAITING_APPROVAL
        step.log.append(f"Awaiting authorisation for {money(step.amount)} on the Card.")
        run.log.append(f"{step.title} needs a payment authorisation before it can be committed.")
        return {"changed": True, "step": step.public(), "reason": "awaiting_payment"}

    item = connectors.INVENTORY_BY_ID.get(step.option_id)
    if item is None:
        step.state = StepState.FAILED
        step.log.append("Inventory item is no longer available upstream.")
        run.state = RunState.FAILED
        run.log.append(f"{step.title} failed — the offer expired before it was committed.")
        return {"changed": True, "step": step.public(), "reason": "failed"}

    step.state = StepState.IN_PROGRESS
    # Idempotency key derived from the run and the step, so a retried call after
    # a timeout cannot double-book.
    receipt = connectors.execute(item, idempotency_key=f"{run.id}:{step.id}")
    step.result = receipt
    step.state = StepState.DONE
    step.log.append(f"{step.action} → {receipt['reference']}")
    if receipt["charged"]:
        step.log.append(f"{money(receipt['charged'])} charged to the Card ending 1005.")
    elif receipt["refunded"]:
        step.log.append(f"{money(receipt['refunded'])} refunded to the Card ending 1005.")
    run.log.append(f"{step.title} confirmed — reference {receipt['reference']}.")

    if _next_step(run) is None:
        run.state = RunState.COMPLETE
        run.log.append("All steps settled. Recovery complete.")

    return {"changed": True, "step": step.public(), "reason": "done"}


# ---------------------------------------------------------------------------
# Compensation
# ---------------------------------------------------------------------------

def rollback_quote(run: ExecutionRun) -> Dict[str, Any]:
    """Ask every committed step what undoing it would actually cost.

    Read-only. Nothing is cancelled here — this exists so the member sees the
    unrecoverable column *before* deciding, rather than discovering it after.
    """
    lines: List[Dict[str, Any]] = []
    refundable = 0.0
    unrecoverable = 0.0

    for step in reversed(run.steps):
        if step.state is not StepState.DONE:
            continue
        item = connectors.INVENTORY_BY_ID.get(step.option_id)
        if item is None:
            continue
        quote = connectors.cancellation_quote(item, step.result)
        refundable += quote["refund_amount"]
        unrecoverable += quote["unrecoverable"]
        lines.append({
            "step_id": step.id,
            "title": step.title,
            "supplier": item.supplier,
            "connector": step.connector,
            "compensating_action": step.compensating_action,
            "quote": quote,
        })

    pending = [s.public() for s in run.steps if s.state in (StepState.PENDING, StepState.AWAITING_APPROVAL)]

    return {
        "run_id": run.id,
        "committed": lines,
        "will_be_skipped": pending,
        "refundable_total": round(refundable, 2),
        "unrecoverable_total": round(unrecoverable, 2),
        "fully_reversible": unrecoverable == 0.0,
        "note": (
            "Everything committed so far can be reversed in full."
            if unrecoverable == 0.0
            else f"{money(unrecoverable)} cannot be recovered — suppliers do not refund all of it."
        ),
    }


def cancel(run: ExecutionRun, *, rollback: bool) -> Dict[str, Any]:
    """Stop the run. With ``rollback``, also compensate what has been committed.

    Compensation runs in reverse order, because the dependencies that made the
    forward order necessary run the other way when you unwind.
    """
    if run.state in (RunState.CANCELLED, RunState.ROLLED_BACK):
        return {"changed": False, "run": run.public()}

    run.state = RunState.CANCELLING

    for step in run.steps:
        if step.state in (StepState.PENDING, StepState.AWAITING_APPROVAL):
            step.state = StepState.SKIPPED
            step.log.append("Skipped — recovery was cancelled before this step ran.")

    if not rollback:
        run.state = RunState.CANCELLED
        run.log.append(
            "Recovery stopped. Steps already committed are left in place — "
            "nothing was reversed."
        )
        return {"changed": True, "run": run.public(), "refunded": 0.0, "unrecoverable": 0.0}

    refunded = 0.0
    unrecoverable = 0.0

    for step in reversed(run.steps):
        if step.state is not StepState.DONE:
            continue
        item = connectors.INVENTORY_BY_ID.get(step.option_id)
        if item is None:
            step.state = StepState.COMPENSATION_FAILED
            step.log.append("Cannot compensate — the supplier reference is no longer resolvable.")
            continue

        step.state = StepState.COMPENSATING
        quote = connectors.cancellation_quote(item, step.result)
        result = connectors.confirm_cancellation(item, quote)
        step.compensation = {**quote, **result}

        if result["ok"]:
            step.state = StepState.COMPENSATED
            refunded += result["refunded"]
            unrecoverable += result["unrecoverable"]
            step.log.append(
                f"{step.compensating_action} → refunded {money(result['refunded'])}"
                + (f", {money(result['unrecoverable'])} unrecoverable" if result["unrecoverable"] else "")
            )
        else:
            step.state = StepState.COMPENSATION_FAILED
            # Trust the quote's own figure. A step that gave a booking up had
            # money come *back* on the way in, so the exposure is what it would
            # cost to buy it again — not what was charged, which was nothing.
            unrecoverable += float(quote.get("unrecoverable", 0.0))
            step.log.append(f"{item.supplier} cannot reverse this automatically — {quote['note']}")

    run.state = RunState.ROLLED_BACK
    run.log.append(
        f"Rollback complete. {money(refunded)} refunded to the Card"
        + (f"; {money(unrecoverable)} could not be recovered." if unrecoverable else " in full.")
    )
    return {
        "changed": True,
        "run": run.public(),
        "refunded": round(refunded, 2),
        "unrecoverable": round(unrecoverable, 2),
    }
