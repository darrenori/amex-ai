"""The Travel Recovery Orchestrator.

It owns the workflow and nothing else. Reasoning about inventory belongs to the
agents, arithmetic about reachability belongs to the graph, transactions belong
to the execution engine. This module decides *what happens next*:

    1  detect          sweep the member's upcoming flights for a disruption
    2  reconstruct     rebuild the itinerary from the member's own booking history
    3  assess          propagate the disruption and find what actually broke
    4  create tasks    one per affected booking, with real constraints on it
    5  delegate        fan out to the specialized agents, concurrently
    6  assemble        combine agent options into whole candidate plans
    7  optimize        Pareto front plus a scalarised ranking
    8  validate        re-check any plan, including one the member edited by hand
    9  approve         freeze a snapshot and hand it to the execution engine

Step 6 is the one that is easy to get wrong. Asking each agent for its single
best option and stapling the results together produces a plan nobody chose:
the cheapest flight next to the least-disruptive hotel change next to the
fastest transfer. Instead each *strategy* is assembled end to end, so every
candidate is internally coherent and the member is comparing whole answers.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import agents, catalog, connectors, optimizer
from .catalog import JST, SGT
from .domain import (
    Booking,
    BookingKind,
    BookingStatus,
    Flexibility,
    Option,
    PlanMetrics,
    Priority,
    RecoveryPlan,
    RecoveryTask,
    Severity,
    TaskState,
    Verdict,
    Violation,
)
from .graph import Itinerary, affected, propagate, total_exposure

# The moment the demonstration is anchored at: three hours before SQ638 was due
# to push back, which is when the airline notified.
NOW = datetime(2026, 9, 18, 6, 2, tzinfo=SGT)

STRATEGIES = ("time", "cost", "disruption", "balanced")

# Consumer surplus on a booked experience, as a multiple of what was paid. A
# purchase reveals willingness-to-pay at or above the price, never below it, so
# valuing the loss at exactly the price systematically under-prices giving it up.
# 1.5 is a stated assumption rather than a measurement — it is here as one number
# to argue with instead of an implicit 1.0 buried in a sum.
EXPERIENCE_SURPLUS = 1.5


# ---------------------------------------------------------------------------
# 1 · Detection
# ---------------------------------------------------------------------------

def detect(itinerary: Itinerary, *, now: datetime = NOW) -> Dict[str, Any]:
    """Sweep every upcoming flight through the status connector.

    In production this is a scheduled poll plus AeroDataBox's flight-alert
    webhook, so the member is told before they think to ask. The "Check my
    flights" button runs the same sweep on demand.
    """
    checks: List[Dict[str, Any]] = []
    disruptions: List[Dict[str, Any]] = []

    for booking in sorted(itinerary.bookings.values(), key=lambda b: b.start):
        if booking.kind is not BookingKind.FLIGHT:
            continue
        number = booking.meta.get("flight_number")
        if not number:
            continue

        result = connectors.fetch_flight_status(number, booking.start.strftime("%Y-%m-%d"))
        disruptive = result["status"] in connectors.DISRUPTIVE_STATUSES
        checks.append({
            "booking_id": booking.id,
            "flight_number": number,
            "date": booking.start.strftime("%Y-%m-%d"),
            "status": result["status"],
            "source": result["source"],
            "endpoint": result["endpoint"],
            "disruptive": disruptive,
            "raw": result.get("flight"),
        })

        if disruptive:
            reason = ((result.get("flight") or {}).get("_disruption") or {}).get(
                "reason", "Withdrawn by the carrier."
            )
            disruptions.append({
                "id": f"DSR-{booking.id[-4:].upper()}",
                "booking_id": booking.id,
                "kind": "flight_cancellation",
                "status": result["status"],
                "headline": f"{booking.title} · {result['status']}",
                "reason": reason,
                "detected_at": now.isoformat(),
                "source": result["source"],
                "upstream": result["upstream"],
                "endpoint": result["endpoint"],
            })

    return {
        "checked_at": now.isoformat(),
        "connector": connectors.STATUS_SPEC.public(),
        "checks": checks,
        "disruptions": disruptions,
        "clean": not disruptions,
    }


# ---------------------------------------------------------------------------
# 3 · Assessment
# ---------------------------------------------------------------------------

def assess(itinerary: Itinerary, cancelled: Sequence[str]) -> Dict[str, Any]:
    verdicts = propagate(itinerary, cancelled=cancelled)
    affected_ids = affected(verdicts)
    return {
        "verdicts": {bid: v.public() for bid, v in verdicts.items()},
        "affected": affected_ids,
        "unaffected": [bid for bid in itinerary.bookings if bid not in affected_ids],
        "exposure": round(total_exposure(verdicts), 2),
        "_raw": verdicts,
    }


# ---------------------------------------------------------------------------
# 4 · Task creation
# ---------------------------------------------------------------------------

def _root_task(itinerary: Itinerary, booking_id: str, priority: str) -> RecoveryTask:
    booking = itinerary.bookings[booking_id]
    # The tightest downstream hard constraint becomes the target arrival. Here
    # that is the dated park passport, twelve hours ahead of a 09:00 entry.
    targets = [
        itinerary.bookings[edge.target].start - edge.min_buffer
        for edge in itinerary.outgoing[booking_id]
        if edge.severity is Severity.HARD
    ]
    return RecoveryTask(
        id=f"task_{booking_id}",
        booking_id=booking_id,
        agent=agents.agent_for(booking.kind).name,
        objective="Replace the cancelled leg, minimising whole-trip impact",
        constraints={
            "priority": priority,
            "not_before": (NOW + timedelta(hours=2)).isoformat(),
            "arrive_before": min(targets).isoformat() if targets else None,
            "arrive_before_binding": False,
            "cabin": booking.meta.get("cabin"),
            "max_options": 4,
        },
        tools=list(connectors.SPECS[connectors.connector_for(booking.kind)].tools),
    )


def _downstream_task(
    itinerary: Itinerary,
    booking_id: str,
    verdict: Verdict,
    root_option: Option,
    priority: str,
) -> RecoveryTask:
    booking = itinerary.bookings[booking_id]
    connector = connectors.connector_for(booking.kind)

    # The buffer this booking's own incoming edge demands — not a constant.
    buffers = [
        int(edge.min_buffer.total_seconds() // 60)
        for edge in itinerary.incoming[booking_id]
        if edge.source == root_option.booking_id
    ]

    constraints: Dict[str, Any] = {
        "priority": priority,
        "arrival": root_option.end.isoformat(),
        "arrival_buffer_minutes": max(buffers) if buffers else 60,
        "disrupted_at": NOW.isoformat(),
        "replacement_departure": root_option.start.isoformat(),
        "max_options": 4,
    }
    if booking.flexibility is Flexibility.SHIFTABLE:
        cutoff = booking.meta.get("latest_check_in") or booking.meta.get("latest_start")
        if cutoff:
            constraints["latest_check_in"] = cutoff

    # A dated activity also has to finish in time for whatever comes after it.
    onward = [
        itinerary.bookings[edge.target].start - edge.min_buffer
        for edge in itinerary.outgoing[booking_id]
        if edge.severity is Severity.HARD
    ]
    if onward:
        constraints["must_end_before"] = min(onward).isoformat()

    if booking.kind is BookingKind.DINING:
        constraints["in_city_until"] = itinerary.bookings["bk_flight_dom"].start.isoformat() \
            if "bk_flight_dom" in itinerary.bookings else None

    return RecoveryTask(
        id=f"task_{booking_id}_{root_option.id[-6:]}",
        booking_id=booking_id,
        agent=agents.agent_for(booking.kind).name,
        objective=f"Restore “{booking.label}” given the replacement arrival",
        constraints=constraints,
        depends_on=[f"task_{root_option.booking_id}"],
        tools=list(connectors.SPECS[connector].tools),
    )


# ---------------------------------------------------------------------------
# 6 · Plan assembly
# ---------------------------------------------------------------------------

def _pick(options: List[Option], strategy: str) -> Optional[Option]:
    if not options:
        return None
    if strategy == "cost":
        key = lambda o: (o.cost_delta, -o.quality)
    elif strategy == "time":
        key = lambda o: (o.hours_lost, -o.quality, o.cost_delta)
    elif strategy == "disruption":
        key = lambda o: (int(o.changes_booking), int(o.drops_booking), -o.quality, o.cost_delta)
    else:
        key = lambda o: (-o.quality + o.cost_delta / 500.0, o.cost_delta)
    return sorted(options, key=key)[0]


def materialize(
    itinerary: Itinerary,
    cancelled: Sequence[str],
    selections: Dict[str, str],
    catalogue: Dict[str, Option],
    *,
    plan_id: str,
    version: int = 1,
    name: str = "",
    strategy: str = "custom",
    origin: str = "generated",
) -> RecoveryPlan:
    """Turn a set of chosen options into a plan, with its violations and its
    arithmetic recomputed from the graph.

    This is the single place a plan's validity is decided. The frontend can drag
    blocks around all it likes; it never gets to declare the result feasible.
    """
    chosen = {bid: catalogue[oid] for bid, oid in selections.items() if oid in catalogue}

    replacements: Dict[str, Tuple[datetime, datetime]] = {}
    dropped: List[str] = []
    added: List[Option] = []

    for booking_id, option in chosen.items():
        if option.optional or booking_id not in itinerary.bookings:
            added.append(option)
        elif option.drops_booking:
            dropped.append(booking_id)
        elif option.changes_booking:
            replacements[booking_id] = (option.start, option.end)
        # A "keep this booking" option is not a replacement. Feeding its
        # aspirational times into the graph would let the plan assert a check-in
        # the member cannot make; leaving the node alone means it is checked
        # against its real times, which is the whole point of validating.

    working = itinerary.splice_out(dropped)
    replacements = {bid: times for bid, times in replacements.items() if bid in working.bookings}
    still_cancelled = [bid for bid in cancelled if bid in working.bookings and bid not in replacements]

    verdicts = propagate(working, cancelled=still_cancelled, replacements=replacements)

    violations: List[Violation] = []
    forfeited = 0.0
    for booking_id, verdict in verdicts.items():
        if verdict.status is BookingStatus.BROKEN:
            violations.append(Violation(
                booking_id=booking_id,
                severity=Severity.HARD,
                message=verdict.reason,
                edge=verdict.violated_edge,
            ))
            forfeited += verdict.exposure
        elif verdict.status is BookingStatus.CANCELLED:
            violations.append(Violation(
                booking_id=booking_id,
                severity=Severity.HARD,
                message="Still cancelled — no replacement was chosen for it.",
            ))
            forfeited += verdict.exposure
        elif verdict.status is BookingStatus.AT_RISK and verdict.slack_minutes is not None and verdict.slack_minutes < 0:
            violations.append(Violation(
                booking_id=booking_id,
                severity=Severity.SOFT,
                message=verdict.reason,
                edge=verdict.violated_edge,
            ))

    # Money the member gives up by dropping a booking they had already paid for.
    for booking_id in dropped:
        option = chosen.get(booking_id)
        booking = itinerary.bookings[booking_id]
        if option and option.cost_delta >= 0:
            forfeited += max(booking.amount - booking.refundable, 0.0)

    # Nights and tickets written off by an option that keeps the booking but
    # gives part of it up — a hotel amendment that releases the first night.
    for option in chosen.values():
        item = connectors.INVENTORY_BY_ID.get(option.id)
        if item:
            forfeited += float(item.meta.get("forfeited", 0.0))

    cost_delta = sum(option.cost_delta for option in chosen.values()) + forfeited_uncovered(
        itinerary, verdicts, chosen
    )
    refund = sum(-o.cost_delta for o in chosen.values() if o.cost_delta < 0)
    changed = sum(1 for o in chosen.values() if o.changes_booking or o.optional)

    root = next((o for o in chosen.values() if o.booking_id in cancelled), None)
    arrival = root.end if root else None
    hours_lost = root.hours_lost if root else 0.0

    # Fragility compounds. A plan is only as reliable as the product of its legs
    # holding, so a direct flight followed by the last train of the night is not
    # a low-risk plan just because the flight is.
    #
    # Released bookings are *not* excluded from this product. Giving up the
    # reserved transfer does not make the evening more reliable — it means the
    # member is now on an unmanaged fallback, and that fallback carries its own
    # risk, which is what the option's own figure encodes. Excluding them would
    # make "cancel everything" read as the most dependable plan available.
    survival = 1.0
    for option in chosen.values():
        survival *= (1.0 - min(max(option.reliability_risk, 0.0), 1.0))
    reliability_risk = 1.0 - survival

    # Things bought for their own sake and then given up. Instrumental bookings
    # — a transfer, a room — are not counted: losing the train is not losing an
    # experience, it is losing a means, and the graph already prices whether the
    # member can still get where they were going.
    #
    # Valued at a premium over the price paid, not at the price paid. Someone who
    # buys a park passport at SGD 80 has revealed they value the day at *at
    # least* SGD 80 — pricing the loss at exactly SGD 80 makes the refund
    # perfectly cancel it out, so the optimizer reads "delete the day, take the
    # money back" as free. It is not free; that is the whole reason they bought
    # the ticket. EXPERIENCE_SURPLUS is the assumption made explicit.
    given_up = sum(
        itinerary.bookings[bid].amount
        for bid in dropped
        if itinerary.bookings[bid].kind in (BookingKind.ACTIVITY, BookingKind.DINING)
    )
    given_up += sum(
        itinerary.bookings[bid].amount
        for bid, verdict in verdicts.items()
        if bid not in chosen
        and verdict.status in (BookingStatus.BROKEN, BookingStatus.CANCELLED)
        and bid in itinerary.bookings
        and itinerary.bookings[bid].kind in (BookingKind.ACTIVITY, BookingKind.DINING)
    )
    experience_lost = given_up * EXPERIENCE_SURPLUS

    plan = RecoveryPlan(
        id=plan_id,
        version=version,
        name=name or "Custom plan",
        strategy=strategy,
        summary=_summarize(chosen, root),
        selections=dict(selections),
        metrics=PlanMetrics(
            cost_delta=round(cost_delta, 2),
            hours_lost=round(hours_lost, 2),
            bookings_changed=changed,
            forfeited=round(forfeited, 2),
            refund_expected=round(refund, 2),
            arrival=arrival,
            experience_lost=round(experience_lost, 2),
            bookings_dropped=len(dropped),
            reliability_risk=round(reliability_risk, 4),
        ),
        violations=violations,
        origin=origin,
    )
    return plan


def forfeited_uncovered(
    itinerary: Itinerary,
    verdicts: Dict[str, Verdict],
    chosen: Dict[str, Option],
) -> float:
    """Non-refundable money on bookings the plan leaves broken.

    A plan that simply ignores a broken booking is not free; it writes that
    booking off. Counting it here is what stops "do nothing about the hotel"
    from scoring better than "move the hotel".
    """
    total = 0.0
    for booking_id, verdict in verdicts.items():
        if booking_id in chosen:
            continue
        if verdict.status in (BookingStatus.BROKEN, BookingStatus.CANCELLED):
            total += verdict.exposure
    return total


def _summarize(chosen: Dict[str, Option], root: Optional[Option]) -> str:
    if root is None:
        return "No replacement selected for the cancelled leg."
    touched = [o for o in chosen.values() if o is not root and (o.changes_booking or o.optional)]
    if not touched:
        return f"{root.title}. Nothing else on the trip has to move."
    labels = ", ".join(sorted({o.kind.value for o in touched}))
    return f"{root.title}, plus changes to {len(touched)} downstream booking(s): {labels}."


# ---------------------------------------------------------------------------
# 5–7 · The full planning pass
# ---------------------------------------------------------------------------

async def plan(
    itinerary: Itinerary,
    cancelled: Sequence[str],
    *,
    priority: str = Priority.INFERRED.value,
    profile_id: str = "time",
) -> Dict[str, Any]:
    """Run the whole planning workflow and return everything the UI needs to
    show its work: the tasks, the agent trace, every option, and the ranked
    candidate plans."""

    assessment = assess(itinerary, cancelled)
    verdicts: Dict[str, Verdict] = assessment["_raw"]

    root_id = cancelled[0]
    root_task = _root_task(itinerary, root_id, priority)
    root_options = await agents.agent_for(itinerary.bookings[root_id].kind).run(root_task, itinerary)

    tasks: List[RecoveryTask] = [root_task]
    catalogue: Dict[str, Option] = {o.id: o for o in root_options}
    plans: List[RecoveryPlan] = []
    seen: set = set()

    for root_option in root_options:
        # Re-propagate under this specific replacement. Different replacements
        # break different things — that is the whole reason to do it per-option
        # rather than once up front.
        local = propagate(itinerary, replacements={root_id: (root_option.start, root_option.end)})
        downstream = [bid for bid in affected(local) if bid != root_id]

        wave = [
            _downstream_task(itinerary, bid, local[bid], root_option, priority)
            for bid in downstream
        ]
        results = await asyncio.gather(*(
            agents.agent_for(itinerary.bookings[t.booking_id].kind).run(t, itinerary)
            for t in wave
        ))
        tasks.extend(wave)

        by_booking: Dict[str, List[Option]] = {}
        for option_list in results:
            for option in option_list:
                catalogue[option.id] = option
                by_booking.setdefault(option.booking_id, []).append(option)

        for strategy in STRATEGIES:
            selections = {root_id: root_option.id}
            for booking_id, options in by_booking.items():
                if options and options[0].optional and strategy == "cost":
                    continue          # a nice-to-have room is the first thing cost drops
                pick = _pick(options, strategy)
                if pick:
                    selections[booking_id] = pick.id

            fingerprint = frozenset(selections.items())
            if fingerprint in seen:
                continue
            seen.add(fingerprint)

            candidate = materialize(
                itinerary, cancelled, selections, catalogue,
                plan_id=f"plan_{len(plans) + 1:02d}",
                name=_plan_name(root_option, strategy),
                strategy=strategy,
            )
            candidate.summary = f"{root_option.title} ({_plan_timing(root_option)}). {candidate.summary}"
            plans.append(candidate)

    ranking = optimizer.rank(plans, priority, profile_id)

    return {
        "now": NOW.isoformat(),
        "assessment": {k: v for k, v in assessment.items() if k != "_raw"},
        # In dependency order, so the editor's lanes read down the trip without
        # having to re-derive the ordering in the browser.
        "bookings": [itinerary.bookings[bid].public() for bid in itinerary.topological_order()],
        "tasks": [t.public() for t in tasks],
        "agents": agents.agent_roster(),
        "connectors": connectors.connector_report(),
        "options": [o.public() for o in catalogue.values()],
        "plans": [p.public() for p in plans],
        "ranking": ranking,
        "priority": priority,
        "profile_id": profile_id,
    }


_STRATEGY_LABEL = {
    "time": "fastest",
    "cost": "cheapest",
    "disruption": "least disruption",
    "balanced": "balanced",
}


def _plan_name(root_option: Option, strategy: str) -> str:
    """Named for the replacement flight, then the downstream strategy.

    The flight has to lead: it is the decision the member recognises, and two
    plans built on different flights are different answers even when the
    downstream strategy matches.
    """
    code = root_option.title.split(" · ")[0]
    return f"{code} · {_STRATEGY_LABEL[strategy]}"


def _plan_timing(root_option: Option) -> str:
    when = root_option.start
    days = (when.date() - NOW.date()).days
    if days == 0:
        return "same day"
    if days == 1:
        return "next morning" if when.hour < 12 else "next evening"
    return f"in {days} days"
