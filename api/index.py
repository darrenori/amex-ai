"""TripShield API — the Travel Recovery Orchestrator's HTTP surface.

Routes only. Every decision lives in ``api/tripshield/``; this module maps it
onto endpoints and does the request validation.

Deployment
----------
Vercel detects ``api/index.py`` and serves the ASGI ``app`` below as a Python
serverless function. ``vercel.json`` rewrites ``/api/*`` onto it, so every route
is declared with its full ``/api/...`` path.

Local development
-----------------
    uvicorn api.index:app --reload --port 8000

Vite proxies ``/api`` to that port (see ``vite.config.js``).

Everything is synthetic demonstration data. No booking, payment, cancellation or
claim is real. The one exception is flight status: set ``AERODATABOX_API_KEY``
and ``/api/flights/status`` queries AeroDataBox for real, because reading a
flight's status is free and read-only.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .tripshield import agents, catalog, connectors, execution, optimizer, orchestrator, store
from .tripshield.catalog import CURRENCY, DEMO_CREDENTIALS, DISCLAIMER, MEMBER
from .tripshield.domain import BookingKind, Option, Priority

API = "/api"

app = FastAPI(
    title="TripShield API",
    description=(
        "Travel Recovery Orchestrator for American Express Card Members. "
        "Independent AMEX AI Hackathon 2026 concept — synthetic data only."
    ),
    version="0.2.0",
    docs_url=f"{API}/docs",
    openapi_url=f"{API}/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: str = Field(default="", max_length=254)
    password: str = Field(default="", max_length=254)


class PlanRequest(BaseModel):
    session_id: str = Field(default="demo", max_length=64)
    priority: str = Field(default=Priority.INFERRED.value)
    profile_id: str = Field(default="time", max_length=32)


class ValidateRequest(BaseModel):
    session_id: str = Field(default="demo", max_length=64)
    selections: Dict[str, str]
    priority: str = Field(default=Priority.INFERRED.value)
    profile_id: str = Field(default="time", max_length=32)
    base_plan_id: Optional[str] = None


class ApproveRequest(BaseModel):
    session_id: str = Field(default="demo", max_length=64)
    plan_id: str


class AdvanceRequest(BaseModel):
    session_id: str = Field(default="demo", max_length=64)
    approve_payment: bool = False


class CancelRequest(BaseModel):
    session_id: str = Field(default="demo", max_length=64)
    rollback: bool = False


def _session(session_id: str):
    return store.get(session_id or "demo")


def _require_run(session_id: str, run_id: str):
    run = store.run(session_id or "demo", run_id)
    if run is None:
        raise HTTPException(
            status_code=410,
            detail=(
                "That execution run is no longer in memory. Runs record real "
                "transactions and are never reconstructed from seed data — "
                "approve the plan again to start a new run."
            ),
        )
    return run


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

@app.get(f"{API}/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "tripshield-api", "version": app.version}


@app.post(f"{API}/auth/login")
def login(payload: LoginRequest) -> Dict[str, Any]:
    """Demo credential check. Nothing here is a real authentication system."""
    email = payload.email.strip().lower()
    if email != DEMO_CREDENTIALS["email"] or payload.password != DEMO_CREDENTIALS["password"]:
        raise HTTPException(
            status_code=401,
            detail="Those demo credentials do not match. Use the details shown below the form.",
        )
    return {
        "token": f"demo_{secrets.token_urlsafe(16)}",
        "member": MEMBER,
        "disclaimer": DISCLAIMER,
    }


@app.get(f"{API}/account")
def account(session_id: str = "demo") -> Dict[str, Any]:
    """Everything the signed-in overview renders."""
    session = _session(session_id)
    bookings = session.itinerary.bookings
    return {
        "member": MEMBER,
        "currency": CURRENCY,
        "transactions": catalog.TRANSACTIONS,
        "benefits": catalog.BENEFITS,
        "trip": {
            **catalog.TRIP_META,
            "total": catalog.trip_total(bookings),
            "bookings": [bookings[bid].public() for bid in session.itinerary.topological_order()],
        },
        "disclaimer": DISCLAIMER,
    }


@app.get(f"{API}/bookings")
def bookings(session_id: str = "demo") -> Dict[str, Any]:
    """The single source of truth. Because every component of this trip went on
    the same Card, the platform can answer for the whole journey at once —
    which is what makes the dependency graph buildable."""
    session = _session(session_id)
    return {
        "currency": CURRENCY,
        "trip": {**catalog.TRIP_META, "total": catalog.trip_total(session.itinerary.bookings)},
        "bookings": [session.itinerary.bookings[bid].public() for bid in session.itinerary.topological_order()],
        "disclaimer": DISCLAIMER,
    }


@app.get(f"{API}/benefits")
def benefits() -> Dict[str, Any]:
    return {"benefits": catalog.BENEFITS, "travel_credit": MEMBER["travel_credit"], "currency": CURRENCY}


@app.get(f"{API}/profiles")
def profiles() -> Dict[str, Any]:
    return {"profiles": catalog.PROFILES, "currency": CURRENCY}


@app.get(f"{API}/connectors")
def connector_report() -> Dict[str, Any]:
    """Which MCP servers exist, what they are clients for, and whether each is
    running live or against fixtures right now."""
    return {"connectors": connectors.connector_report(), "agents": agents.agent_roster()}


# ---------------------------------------------------------------------------
# 1 · Detection
# ---------------------------------------------------------------------------

@app.get(f"{API}/flights/status")
def flight_status(
    flight: str = Query(..., max_length=10, description="IATA flight number, e.g. SQ638"),
    date: str = Query(..., max_length=10, description="Local departure date, YYYY-MM-DD"),
) -> Dict[str, Any]:
    """Straight passthrough to the flight-status connector, so the exact upstream
    payload is inspectable. Live against AeroDataBox when a key is configured."""
    return connectors.fetch_flight_status(flight, date)


@app.post(f"{API}/disruption/detect")
def detect(session_id: str = "demo") -> Dict[str, Any]:
    """Sweep every upcoming flight on the trip. This is what the scheduled
    monitor runs; the UI's "Check my flights" fires the same code path."""
    session = _session(session_id)
    result = orchestrator.detect(session.itinerary)
    session.cancelled = [d["booking_id"] for d in result["disruptions"]]
    return {**result, "currency": CURRENCY, "disclaimer": DISCLAIMER}


# ---------------------------------------------------------------------------
# 2–3 · Graph and impact
# ---------------------------------------------------------------------------

@app.get(f"{API}/graph")
def graph(session_id: str = "demo") -> Dict[str, Any]:
    """The reconstructed dependency graph, plus each node's fate under the
    detected disruption. Severity is the column that matters: a violated HARD
    edge invalidates the target, a violated SOFT edge only degrades it."""
    session = _session(session_id)
    payload = session.itinerary.public()
    if session.cancelled:
        assessment = orchestrator.assess(session.itinerary, session.cancelled)
        payload["assessment"] = {k: v for k, v in assessment.items() if k != "_raw"}
    else:
        payload["assessment"] = None
    payload["currency"] = CURRENCY
    payload["cancelled"] = session.cancelled
    return payload


# ---------------------------------------------------------------------------
# 4–7 · Planning
# ---------------------------------------------------------------------------

@app.post(f"{API}/recovery/plan")
async def build_plan(payload: PlanRequest) -> Dict[str, Any]:
    """Create the recovery tasks, delegate them to the agents, assemble whole
    candidate plans and rank them. Returns the trace as well as the result, so
    the UI can show its work rather than assert it."""
    session = _session(payload.session_id)
    if not session.cancelled:
        session.cancelled = [d["booking_id"] for d in orchestrator.detect(session.itinerary)["disruptions"]]
    if not session.cancelled:
        raise HTTPException(status_code=409, detail="No disruption has been detected on this trip.")

    result = await orchestrator.plan(
        session.itinerary,
        session.cancelled,
        priority=payload.priority,
        profile_id=payload.profile_id,
    )

    session.catalogue = {o.id: o for o in _options_from(result)}
    session.plans = {}
    session.last_planning = result
    for public_plan in result["plans"]:
        session.plans[public_plan["id"]] = _rehydrate(session, public_plan)

    return {**result, "currency": CURRENCY, "disclaimer": DISCLAIMER, "session_id": session.id}


@app.get(f"{API}/recovery/rank")
def rerank(
    session_id: str = "demo",
    priority: str = Priority.INFERRED.value,
    profile_id: str = "time",
) -> Dict[str, Any]:
    """Re-score plans already generated. Changing the weighting must not change
    which plans exist — only the order they are presented in."""
    session = _session(session_id)
    if not session.plans:
        raise HTTPException(status_code=409, detail="No plans have been generated for this session yet.")

    plans = list(session.plans.values())
    ranking = optimizer.rank(plans, priority, profile_id)
    return {
        "currency": CURRENCY,
        "plans": [p.public() for p in plans],
        "ranking": ranking,
        "priority": priority,
        "profile_id": profile_id,
    }


@app.post(f"{API}/recovery/plan/validate")
def validate(payload: ValidateRequest) -> Dict[str, Any]:
    """Re-check an arbitrary set of selections — including one the member built
    by dragging blocks around.

    The frontend is never the authority on whether a plan works. It proposes;
    this recomputes the graph, the violations and the arithmetic from scratch.
    """
    session = _session(payload.session_id)
    if not session.catalogue:
        raise HTTPException(status_code=409, detail="No plans have been generated for this session yet.")

    unknown = [oid for oid in payload.selections.values() if oid not in session.catalogue]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown option(s): {', '.join(sorted(unknown))}")

    session.plan_version += 1
    base = session.plans.get(payload.base_plan_id) if payload.base_plan_id else None

    plan = orchestrator.materialize(
        session.itinerary,
        session.cancelled,
        payload.selections,
        session.catalogue,
        plan_id=f"plan_edit_{session.plan_version:02d}",
        version=session.plan_version,
        name=f"Edited from {base.name}" if base else "Custom plan",
        strategy="custom",
        origin="edited",
    )

    weights = optimizer.weights_for(payload.priority, payload.profile_id)
    plan.score = round(optimizer.score(plan, weights), 2)
    plan.score_breakdown = optimizer.breakdown(plan, weights)
    session.plans[plan.id] = plan

    comparison = None
    if base:
        comparison = {
            "base_plan_id": base.id,
            "cost_delta": round(plan.metrics.cost_delta - base.metrics.cost_delta, 2),
            "hours_delta": round(plan.metrics.hours_lost - base.metrics.hours_lost, 2),
            "changed_delta": plan.metrics.bookings_changed - base.metrics.bookings_changed,
            "score_delta": round(plan.score - base.score, 2),
        }

    return {
        "currency": CURRENCY,
        "plan": plan.public(),
        "weights": weights.public(),
        "comparison": comparison,
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# 9–12 · Approval, execution, compensation
# ---------------------------------------------------------------------------

@app.post(f"{API}/recovery/plan/approve")
def approve(payload: ApproveRequest) -> Dict[str, Any]:
    """Freeze the chosen plan into an execution run. Nothing has been
    transacted yet — approval creates the queue, ``/advance`` works through it."""
    session = _session(payload.session_id)
    plan = session.plans.get(payload.plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Unknown plan '{payload.plan_id}'.")
    if not plan.valid:
        raise HTTPException(
            status_code=422,
            detail="That plan leaves a hard dependency unsatisfied and cannot be approved.",
        )

    run = execution.build_run(session.itinerary, plan, session.catalogue)
    session.runs[run.id] = run
    return {"currency": CURRENCY, "run": run.public(), "disclaimer": DISCLAIMER}


@app.get(f"{API}/execution/{{run_id}}")
def get_run(run_id: str, session_id: str = "demo") -> Dict[str, Any]:
    return {"currency": CURRENCY, "run": _require_run(session_id, run_id).public()}


@app.post(f"{API}/execution/{{run_id}}/advance")
def advance(run_id: str, payload: AdvanceRequest) -> Dict[str, Any]:
    """Execute exactly one transition. Steps that cost money stop for an
    authorisation and do not move again until this is called with
    ``approve_payment``."""
    run = _require_run(payload.session_id, run_id)
    result = execution.advance(run, approve_payment=payload.approve_payment)
    return {"currency": CURRENCY, "result": result, "run": run.public()}


@app.get(f"{API}/execution/{{run_id}}/rollback-quote")
def rollback_quote(run_id: str, session_id: str = "demo") -> Dict[str, Any]:
    """What undoing the committed steps would actually return. Read-only.

    This exists because rollback is not free and is not always possible — the
    member has to see the unrecoverable column before they decide, not after.
    """
    return {"currency": CURRENCY, "quote": execution.rollback_quote(_require_run(session_id, run_id))}


@app.post(f"{API}/execution/{{run_id}}/cancel")
def cancel(run_id: str, payload: CancelRequest) -> Dict[str, Any]:
    """Two different meanings behind one button.

    ``rollback=false`` stops future steps and leaves committed ones alone.
    ``rollback=true`` also runs compensating transactions in reverse order.
    """
    run = _require_run(payload.session_id, run_id)
    result = execution.cancel(run, rollback=payload.rollback)
    return {"currency": CURRENCY, **result}


@app.post(f"{API}/session/reset")
def reset(session_id: str = "demo") -> Dict[str, Any]:
    store.reset(session_id)
    return {"ok": True, "session_id": session_id}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _options_from(result: Dict[str, Any]) -> List[Any]:
    """The orchestrator returns options as plain dicts for the wire; the session
    keeps the objects so plans can be re-materialised without a second pass."""
    options = []
    for row in result["options"]:
        options.append(Option(
            id=row["id"],
            task_id=row["task_id"],
            booking_id=row["booking_id"],
            kind=BookingKind(row["kind"]),
            agent=row["agent"],
            connector=row["connector"],
            title=row["title"],
            detail=row["detail"],
            supplier=row["supplier"],
            supplier_offer_id=row["supplier_offer_id"],
            start=datetime.fromisoformat(row["start"]),
            end=datetime.fromisoformat(row["end"]),
            location=row["location"],
            place_code=row["place_code"],
            cost_delta=row["cost_delta"],
            hours_lost=row["hours_lost"],
            changes_booking=row["changes_booking"],
            quality=row["quality"],
            drops_booking=row["drops_booking"],
            optional=row["optional"],
            notes=row["notes"],
            tool_call=row["tool_call"],
        ))
    return options


def _rehydrate(session, public_plan: Dict[str, Any]):
    """Rebuild a plan object from its wire form, so later requests can re-score
    or approve it without re-running the whole planning pass."""
    return orchestrator.materialize(
        session.itinerary,
        session.cancelled,
        public_plan["selections"],
        session.catalogue,
        plan_id=public_plan["id"],
        version=public_plan["version"],
        name=public_plan["name"],
        strategy=public_plan["strategy"],
        origin=public_plan["origin"],
    )
