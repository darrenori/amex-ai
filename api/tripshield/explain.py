"""Reason codes and audit records.

Two obligations that come with letting software re-arrange somebody's trip.

**Reason codes** are a stable, machine-readable vocabulary for *why* a plan was
recommended. Prose explanations are for the member; these are for everything
else, support agents reading a case months later, a regulator asking how the
system decides, a regression test asserting the reasoning did not silently
change. They are derived from the plan's own metrics rather than written by the
thing making the recommendation, so they cannot drift away from the numbers.

**Audit records** capture the decision at the moment of approval: what was
recommended, what the member actually chose, every candidate's score, and which
model versions produced them. This is written when the plan is approved rather
than when it is executed, because the interesting question after the fact is
usually "why was this offered" and not "did the booking succeed".
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .domain import RecoveryPlan
from .optimizer import Weights

# Bumped when the logic that produces a recommendation changes. An audit record
# is worth little if you cannot tell which version of the system wrote it.
MODEL_VERSIONS = {
    "preference": "inferred-preference-v2",
    "reliability": "compounded-leg-risk-v1",
    "impact": "dependency-graph-v2",
    "ranker": "pareto-plus-scalarised-v2",
}

LOW_DELAY_HOURS = 6.0
LOW_RISK_CEILING = 0.18
HIGH_TIME_VALUE = 0.75


def reason_codes(plan: RecoveryPlan, weights: Weights, profile: Optional[Dict[str, Any]] = None) -> List[str]:
    """Why this plan looks the way it does, as stable codes.

    Read off the metrics, never off the recommendation, a code must be true of
    the plan whether or not the plan won.
    """
    m = plan.metrics
    codes: List[str] = []

    if m.cost_delta < 0:
        codes.append("LOWER_WHOLE_TRIP_COST")
    if m.forfeited == 0:
        codes.append("NOTHING_WRITTEN_OFF")
    if m.experience_lost == 0:
        codes.append("EXPERIENCE_PRESERVED")
    if m.bookings_dropped == 0:
        codes.append("NOTHING_GIVEN_UP")
    if m.bookings_changed <= 3:
        codes.append("LOW_CHURN")
    if m.hours_lost <= LOW_DELAY_HOURS:
        codes.append("LOW_DELAY")
    if m.reliability_risk <= LOW_RISK_CEILING:
        codes.append("LOW_RELIABILITY_RISK")
    if plan.pareto_optimal:
        codes.append("PARETO_OPTIMAL")
    if not plan.valid:
        codes.append("HARD_DEPENDENCY_UNSATISFIED")
    if any(v.severity.value == "soft" for v in plan.violations):
        codes.append("TIGHT_CONNECTION")

    if profile and profile.get("time_sensitivity", 0) >= HIGH_TIME_VALUE:
        codes.append("HIGH_CUSTOMER_TIME_VALUE")
    if profile and profile.get("risk_tolerance", 1) <= 0.3:
        codes.append("LOW_CUSTOMER_RISK_TOLERANCE")

    return codes


def audit_record(
    *,
    event: str,
    plan: RecoveryPlan,
    all_plans: List[RecoveryPlan],
    ranking: Dict[str, Any],
    weights: Weights,
    profile: Optional[Dict[str, Any]],
    trip_id: str,
    member_id: str,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """A record of the decision, not of the transaction.

    ``recommended`` and ``selected`` are kept separate on purpose: the gap
    between them is the single most useful signal the system produces about
    whether its ranking matches what members actually want.
    """
    return {
        "event": event,
        "trip_id": trip_id,
        "member_id": member_id,
        "run_id": run_id,
        "recommended_plan_id": ranking.get("recommended_plan_id"),
        "selected_plan_id": plan.id,
        "followed_recommendation": plan.id == ranking.get("recommended_plan_id"),
        "weighting": weights.public(),
        "profile_id": (profile or {}).get("id"),
        "model_versions": dict(MODEL_VERSIONS),
        "scores": {p.id: p.score for p in all_plans},
        "pareto_front": [p.id for p in all_plans if p.pareto_optimal],
        "reason_codes": reason_codes(plan, weights, profile),
        "metrics": plan.metrics.public(),
        "selections": dict(plan.selections),
        "plan_origin": plan.origin,
        "plan_version": plan.version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
