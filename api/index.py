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

Every booking, payment, cancellation and claim is synthetic. Read-only data may
come from AeroDataBox production status, Duffel test offers, or LiteAPI sandbox
rates when the corresponding credential is configured; every transaction still
runs through the fixture simulator.
"""

from __future__ import annotations

import secrets
from dataclasses import fields
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .tripshield import ai, ai_agents, agents, catalog, connectors, execution, explain, optimizer, orchestrator, store
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
    """Report direct adapters, deterministic fallbacks, and bounded AI status."""
    return {
        "connectors": connectors.connector_report(),
        "agents": agents.agent_roster(),
        "ai": ai.ai_status(),
    }


@app.get(f"{API}/connectors/health")
async def connector_health(key: Optional[str] = None) -> Dict[str, Any]:
    """Run bounded, read-only checks; this never books, changes, or cancels."""
    try:
        report = await connectors.connector_health(key, timeout=3.0)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {**report, "ai": ai.ai_status()}


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
        member_history=catalog.PROFILES_BY_ID.get(payload.profile_id) or {},
        safety_identifier=session.id,
    )

    # Supplier offer payloads are request-scoped execution state, never wire
    # output. Freeze them on the session so approval can snapshot the exact
    # sandbox/fixture items without introducing a process-global live cache.
    session.inventory = result.pop("_inventory", {})

    session.catalogue = {o.id: o for o in _options_from(result)}
    session.priority = payload.priority
    session.profile_id = payload.profile_id
    session.plans = {p["id"]: _rehydrate(session, p) for p in result["plans"]}

    # Rank the *stored* objects, not the throwaway ones the orchestrator built.
    # `materialize` deliberately does not score or mark the Pareto front — that
    # is the optimizer's job — so a rehydrated plan starts unscored. Ranking here
    # both fills those fields in and guarantees the ranking the client is shown
    # describes the exact objects a later approval will read.
    plans = list(session.plans.values())
    ranking = optimizer.rank(plans, payload.priority, payload.profile_id)
    graph_context = session.itinerary.public()
    graph_context.update({
        "cancelled": list(session.cancelled),
        "assessment": result["assessment"],
    })
    recommendation = await ai_agents.recommend_plans(
        graph=graph_context,
        plans=[plan.public() for plan in plans],
        ranking=ranking,
        member_history=catalog.PROFILES_BY_ID.get(payload.profile_id) or {},
        specialist_findings=result.get("specialist_findings", []),
        safety_identifier=session.id,
    )
    ranking = optimizer.apply_personalized_ranking(plans, ranking, recommendation)
    result["agent_runs"] = [*result.get("specialist_findings", []), recommendation]
    session.last_ranking = ranking
    result["ranking"] = ranking
    session.last_planning = result

    return {
        **result,
        "plans": [p.public() for p in session.plans.values()],
        "ranking": ranking,
        "currency": CURRENCY,
        "disclaimer": DISCLAIMER,
        "session_id": session.id,
    }


@app.get(f"{API}/recovery/rank")
async def rerank(
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
    graph_context = session.itinerary.public()
    graph_context.update({
        "cancelled": list(session.cancelled),
        "assessment": (session.last_planning or {}).get("assessment", {}),
    })
    recommendation = await ai_agents.recommend_plans(
        graph=graph_context,
        plans=[plan.public() for plan in plans],
        ranking=ranking,
        member_history=catalog.PROFILES_BY_ID.get(profile_id) or {},
        specialist_findings=(session.last_planning or {}).get("specialist_findings", []),
        safety_identifier=session.id,
    )
    ranking = optimizer.apply_personalized_ranking(plans, ranking, recommendation)
    agent_runs = [
        *(session.last_planning or {}).get("specialist_findings", []),
        recommendation,
    ]
    if session.last_planning is not None:
        session.last_planning["agent_runs"] = agent_runs
        session.last_planning["ranking"] = ranking
    session.last_ranking = ranking
    session.priority = priority
    session.profile_id = profile_id
    return {
        "currency": CURRENCY,
        "plans": [p.public() for p in plans],
        "ranking": ranking,
        "agent_runs": agent_runs,
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

    run = execution.build_run(
        session.itinerary, plan, session.catalogue, inventory=session.inventory
    )
    session.runs[run.id] = run

    # Written at approval, not at execution. The question worth answering months
    # later is usually "why was this offered", not "did the booking succeed".
    ranking = session.last_ranking or {"recommended_plan_id": plan.id}
    record = explain.audit_record(
        event="plan_approved",
        plan=plan,
        all_plans=list(session.plans.values()),
        ranking=ranking,
        weights=optimizer.weights_for(session.priority, session.profile_id),
        profile=catalog.PROFILES_BY_ID.get(session.profile_id),
        trip_id=catalog.TRIP_META["id"],
        member_id=MEMBER["name"],
        run_id=run.id,
    )
    record["id"] = f"AUD-{len(session.audit) + 1:04d}"
    # Capture the final personalized recommendation and every bounded agent run
    # as provenance. Prompts, provider credentials and raw secrets are excluded.
    ai_record = ranking.get("ai")
    if isinstance(ai_record, dict):
        record["ai"] = {
            key: ai_record.get(key)
            for key in (
                "status", "provider", "model", "transport", "tools_used",
                "latency_ms", "error_code", "confidence",
                "referenced_plan_ids", "referenced_option_ids",
            )
        }
    record["agent_runs"] = [
        {
            key: run.get(key)
            for key in (
                "role", "specialty", "status", "provider", "model", "transport",
                "tools_used", "task_ids", "latency_ms", "error_code",
                "referenced_plan_ids", "referenced_option_ids",
            )
        }
        for run in (session.last_planning or {}).get("agent_runs", [])
        if isinstance(run, dict)
    ]
    session.audit.append(record)
    run.log.append(
        f"Audit {record['id']} written — "
        + ("followed the recommendation." if record["followed_recommendation"]
           else f"member chose {plan.name} over the recommended {ranking.get('recommended_plan_id')}.")
    )

    return {
        "currency": CURRENCY,
        "run": run.public(),
        "audit": record,
        "reason_codes": record["reason_codes"],
        "disclaimer": DISCLAIMER,
    }


@app.get(f"{API}/audit")
def audit_log(session_id: str = "demo") -> Dict[str, Any]:
    """Every approval decision this session made, newest last.

    Kept as its own endpoint because the audit trail is not part of any one
    plan or run — it is the record of what was recommended versus what was
    chosen, across all of them.
    """
    session = _session(session_id)
    return {"records": session.audit, "model_versions": explain.MODEL_VERSIONS}


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

_OPTION_FIELDS = {f.name for f in fields(Option)}
_OPTION_COERCE = {
    "kind": BookingKind,
    "start": datetime.fromisoformat,
    "end": datetime.fromisoformat,
}


def _options_from(result: Dict[str, Any]) -> List[Option]:
    """The orchestrator returns options as plain dicts for the wire; the session
    keeps the objects so plans can be re-materialised without a second pass.

    Rebuilt generically from the dataclass's own field list rather than a hand
    written argument-by-argument copy. That copy is where a new field silently
    picks up its default instead of its real value — which means the plan the
    member approved would carry different numbers from the plan they were shown.
    """
    options: List[Option] = []
    for row in result["options"]:
        payload = {
            key: _OPTION_COERCE.get(key, lambda value: value)(value)
            for key, value in row.items()
            if key in _OPTION_FIELDS
        }
        # Private execution state is stored separately on the session and is
        # intentionally absent from Option.public().
        payload.setdefault("inventory_snapshot", {})
        missing = _OPTION_FIELDS - payload.keys()
        if missing:
            raise HTTPException(
                status_code=500,
                detail=f"Option payload is missing {sorted(missing)} — Option.public() is out of step.",
            )
        options.append(Option(**payload))
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
