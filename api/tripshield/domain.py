"""Types shared across the recovery orchestrator.

Nothing in here talks to a network or holds mutable global state; it is the
vocabulary the rest of the package agrees on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

CURRENCY = "SGD"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class BookingKind(str, Enum):
    FLIGHT = "flight"
    LODGING = "lodging"
    ACTIVITY = "activity"
    DINING = "dining"
    GROUND = "ground"


class BookingStatus(str, Enum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"      # the supplier cancelled it
    BROKEN = "broken"            # a hard dependency can no longer be satisfied
    AT_RISK = "at_risk"          # a soft dependency is violated
    UNAFFECTED = "unaffected"
    REBOOKED = "rebooked"


class DependencyType(str, Enum):
    TEMPORAL = "temporal"        # B cannot start until A ends (+ buffer)
    SPATIAL = "spatial"          # B happens where A puts you
    RESOURCE = "resource"        # B consumes something A provides (a car, a seat)


class Severity(str, Enum):
    HARD = "hard"                # violation invalidates the booking
    SOFT = "soft"                # violation degrades it; the member may accept it


class Flexibility(str, Enum):
    """How much a booking can absorb a shift before it has to be re-transacted."""

    FIXED = "fixed"              # timed-entry ticket: move it or lose it
    SHIFTABLE = "shiftable"      # hotel check-in: late arrival is a note, not a rebooking
    REBOOKABLE = "rebookable"    # flight, transfer: a new inventory item replaces it


class TaskState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class StepState(str, Enum):
    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    COMPENSATION_FAILED = "compensation_failed"


class RunState(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETE = "complete"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class Priority(str, Enum):
    COST = "cost"
    TIME = "time"
    DISRUPTION = "disruption"
    BALANCED = "balanced"
    INFERRED = "inferred"


# ---------------------------------------------------------------------------
# Bookings and the graph over them
# ---------------------------------------------------------------------------

@dataclass
class Booking:
    """One purchased item. The single source of truth is the member's own
    booking history, because everything on this trip went on one Card."""

    id: str
    kind: BookingKind
    label: str
    title: str
    detail: str
    supplier: str
    supplier_ref: str            # shaped like the real supplier's id (ord_…, lit_…, BR-…)
    connector: str               # which MCP server owns this booking
    start: datetime
    end: datetime
    location: str
    place_code: str
    amount: float
    refundable: float            # how much of `amount` comes back if cancelled now
    flexibility: Flexibility
    status: BookingStatus = BookingStatus.CONFIRMED
    note: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_container(self) -> bool:
        """True when the member is *based here* for the whole window, rather
        than passing through it.

        A flight or a transfer is done with you when it ends, so anything
        downstream is gated on its arrival time. A hotel is not: you can leave
        for the park the morning after check-in without waiting for checkout.
        Reading a stay's end date as "when you become free" is what makes a
        three-night booking look like it blocks the entire trip.
        """
        return self.kind is BookingKind.LODGING

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "label": self.label,
            "title": self.title,
            "detail": self.detail,
            "supplier": self.supplier,
            "supplier_ref": self.supplier_ref,
            "connector": self.connector,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "location": self.location,
            "place_code": self.place_code,
            "amount": self.amount,
            "refundable": self.refundable,
            "flexibility": self.flexibility.value,
            "is_container": self.is_container,
            "status": self.status.value,
            "note": self.note,
            "meta": self.meta,
        }


@dataclass
class Dependency:
    """A directed edge. Not every relationship is a hard dependency — that
    distinction is the whole reason the graph exists rather than a list."""

    source: str
    target: str
    type: DependencyType
    severity: Severity
    min_buffer: timedelta
    rationale: str

    def public(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.type.value,
            "severity": self.severity.value,
            "min_buffer_minutes": int(self.min_buffer.total_seconds() // 60),
            "rationale": self.rationale,
        }


@dataclass
class Verdict:
    """The result of propagating a change through one node."""

    booking_id: str
    status: BookingStatus
    reason: str
    slack_minutes: Optional[int] = None      # negative means the buffer is blown
    violated_edge: Optional[Dependency] = None
    exposure: float = 0.0                    # money lost if nothing is done

    def public(self) -> Dict[str, Any]:
        return {
            "booking_id": self.booking_id,
            "status": self.status.value,
            "reason": self.reason,
            "slack_minutes": self.slack_minutes,
            "violated_edge": self.violated_edge.public() if self.violated_edge else None,
            "exposure": self.exposure,
        }


# ---------------------------------------------------------------------------
# Recovery tasks and the options agents return
# ---------------------------------------------------------------------------

@dataclass
class RecoveryTask:
    """One unit of work the orchestrator hands to a specialized agent."""

    id: str
    booking_id: str
    agent: str
    objective: str
    constraints: Dict[str, Any]
    depends_on: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    state: TaskState = TaskState.CREATED
    option_ids: List[str] = field(default_factory=list)
    log: List[str] = field(default_factory=list)

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "booking_id": self.booking_id,
            "agent": self.agent,
            "objective": self.objective,
            "constraints": self.constraints,
            "depends_on": self.depends_on,
            "tools": self.tools,
            "state": self.state.value,
            "option_ids": self.option_ids,
            "log": self.log,
        }


@dataclass
class Option:
    """A single replacement an agent found through its connector.

    ``cost_delta`` is signed against what the member already paid: negative
    means money comes back.
    """

    id: str
    task_id: str
    booking_id: str
    kind: BookingKind
    agent: str
    connector: str
    title: str
    detail: str
    supplier: str
    supplier_offer_id: str
    start: datetime
    end: datetime
    location: str
    place_code: str
    cost_delta: float
    hours_lost: float
    changes_booking: bool
    quality: float               # 0–1, the agent's own comfort with the option
    drops_booking: bool = False  # removes the booking from the trip rather than moving it
    optional: bool = False       # an *addition* (a transit hotel), not a replacement
    reliability_risk: float = 0.05
    links: List[Dict[str, str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    tool_call: str = ""          # plain-English name of the call, shown in the trace
    tool_endpoint: str = ""      # the raw adapter/REST call behind it, kept for the audit
    # Provenance is kept separate from the connector name. A Duffel-backed
    # option, for example, may still be a recorded fixture when its sandbox is
    # unavailable. The UI and audit trail must be able to say which is which.
    source_mode: str = "fixture"
    source_upstream: str = ""
    source_note: str = ""
    # Present only after an exact/explicit-alias match against the curated
    # official Amex catalogue. An arbitrary travel supplier is never inferred
    # to be an Amex partner.
    amex_partner: Optional[Dict[str, Any]] = None
    synthetic: bool = True
    # Private request/run snapshot used by the fixture transaction simulator.
    # It is deliberately excluded from ``public()``: supplier payload details
    # are execution state, not part of the member-facing option contract.
    inventory_snapshot: Dict[str, Any] = field(default_factory=dict, repr=False)

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "booking_id": self.booking_id,
            "kind": self.kind.value,
            "agent": self.agent,
            "connector": self.connector,
            "title": self.title,
            "detail": self.detail,
            "supplier": self.supplier,
            "supplier_offer_id": self.supplier_offer_id,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "location": self.location,
            "place_code": self.place_code,
            "cost_delta": self.cost_delta,
            "hours_lost": self.hours_lost,
            "changes_booking": self.changes_booking,
            "quality": self.quality,
            "drops_booking": self.drops_booking,
            "optional": self.optional,
            "reliability_risk": self.reliability_risk,
            "links": self.links,
            "notes": self.notes,
            "tool_call": self.tool_call,
            "tool_endpoint": self.tool_endpoint,
            "source_mode": self.source_mode,
            "source_upstream": self.source_upstream,
            "source_note": self.source_note,
            "amex_partner": self.amex_partner,
            "synthetic": self.synthetic,
        }


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

@dataclass
class Violation:
    booking_id: str
    severity: Severity
    message: str
    edge: Optional[Dependency] = None

    def public(self) -> Dict[str, Any]:
        return {
            "booking_id": self.booking_id,
            "severity": self.severity.value,
            "message": self.message,
            "edge": self.edge.public() if self.edge else None,
        }


@dataclass
class PlanMetrics:
    cost_delta: float            # money, signed against the confirmed trip
    hours_lost: float
    bookings_changed: int
    forfeited: float             # non-refundable money written off
    refund_expected: float
    arrival: Optional[datetime]
    # Value destroyed by giving up something bought for its own sake — a park
    # passport, a dinner reservation. Refunding a ticket returns the money but
    # not the day, and a plan that only counts the refund will always look
    # cheaper than one that protects the experience. Priced at what the member
    # paid, on the revealed-preference argument that they valued it at least
    # that much. Deliberately kept out of `cost_delta`, which stays a real
    # money figure the member can reconcile against a statement.
    experience_lost: float = 0.0
    bookings_dropped: int = 0
    # Probability that at least one leg of this plan fails on the day. Combined
    # across the chosen options rather than read off the flight alone: a plan
    # that hangs on the last train of the night is fragile even behind a direct
    # flight.
    reliability_risk: float = 0.0

    def public(self) -> Dict[str, Any]:
        return {
            "cost_delta": self.cost_delta,
            "hours_lost": self.hours_lost,
            "bookings_changed": self.bookings_changed,
            "forfeited": self.forfeited,
            "refund_expected": self.refund_expected,
            "arrival": self.arrival.isoformat() if self.arrival else None,
            "experience_lost": self.experience_lost,
            "bookings_dropped": self.bookings_dropped,
            "reliability_risk": self.reliability_risk,
        }


@dataclass
class RecoveryPlan:
    """A complete answer to the disruption: one option chosen per affected booking."""

    id: str
    version: int
    name: str
    strategy: str                # which objective this candidate was built to win
    summary: str
    selections: Dict[str, str]   # booking_id -> option_id
    metrics: PlanMetrics
    violations: List[Violation] = field(default_factory=list)
    score: float = 0.0
    score_breakdown: str = ""
    pareto_optimal: bool = False
    origin: str = "generated"    # "generated" | "edited"

    @property
    def valid(self) -> bool:
        return not any(v.severity is Severity.HARD for v in self.violations)

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "strategy": self.strategy,
            "summary": self.summary,
            "selections": self.selections,
            "metrics": self.metrics.public(),
            "violations": [v.public() for v in self.violations],
            "score": self.score,
            "score_breakdown": self.score_breakdown,
            "pareto_optimal": self.pareto_optimal,
            "origin": self.origin,
            "valid": self.valid,
        }


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

@dataclass
class ExecutionStep:
    """One transaction against one supplier, in dependency order."""

    id: str
    index: int
    booking_id: str
    option_id: str
    connector: str
    agent: str
    title: str
    detail: str
    action: str                  # the MCP tool the executor will call
    compensating_action: str     # the MCP tool that undoes it
    amount: float
    requires_payment: bool
    state: StepState = StepState.PENDING
    result: Dict[str, Any] = field(default_factory=dict)
    compensation: Dict[str, Any] = field(default_factory=dict)
    log: List[str] = field(default_factory=list)

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "index": self.index,
            "booking_id": self.booking_id,
            "option_id": self.option_id,
            "connector": self.connector,
            "agent": self.agent,
            "title": self.title,
            "detail": self.detail,
            "action": self.action,
            "compensating_action": self.compensating_action,
            "amount": self.amount,
            "requires_payment": self.requires_payment,
            "state": self.state.value,
            "result": self.result,
            "compensation": self.compensation,
            "log": self.log,
        }


@dataclass
class ExecutionRun:
    """The approved snapshot plus the transaction log against it.

    The snapshot matters: the plan can be re-planned while a run is in flight,
    and compensation must undo what was actually executed, not what is currently
    on screen.
    """

    id: str
    plan_snapshot: Dict[str, Any]
    steps: List[ExecutionStep]
    state: RunState = RunState.APPROVED
    approved_at: Optional[datetime] = None
    log: List[str] = field(default_factory=list)
    # Immutable supplier-item snapshots for the options frozen into this run.
    # Keeping these on the run avoids a process-global live-inventory cache and
    # makes later fixture execution/compensation resolve the approved offer.
    inventory: Dict[str, Dict[str, Any]] = field(default_factory=dict, repr=False)

    @property
    def progress(self) -> float:
        """How much of the plan currently stands.

        Only committed steps count. Skipping the rest of a stopped run is not
        progress — a run halted after one of three transactions is a third of
        the way through, not finished — and a reversed step no longer stands, so
        a full rollback correctly returns the bar to zero.
        """
        if not self.steps:
            return 0.0
        return sum(1 for s in self.steps if s.state is StepState.DONE) / len(self.steps)

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "plan": self.plan_snapshot,
            "steps": [s.public() for s in self.steps],
            "state": self.state.value,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "progress": round(self.progress, 4),
            "log": self.log,
        }


# ---------------------------------------------------------------------------
# Formatting shared with the API layer
# ---------------------------------------------------------------------------

def money(amount: float, currency: str = CURRENCY) -> str:
    return f"{'-' if amount < 0 else ''}{currency} {abs(round(amount)):,}"


def signed_money(amount: float, currency: str = CURRENCY) -> str:
    return f"{'+' if amount >= 0 else '-'}{currency} {abs(round(amount)):,}"


def clock(value: datetime) -> str:
    return value.strftime("%d %b %H:%M")
