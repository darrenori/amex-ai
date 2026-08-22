"""Ranking candidate recovery plans.

Five objectives, all minimised, all in units a member can argue with:

    money       whole-trip financial impact, signed against the confirmed trip
    time        hours of the trip given up
    disruption  how many separate bookings have to be re-transacted
    experience  value destroyed by dropping something bought for its own sake
    fragility   compounded chance the recovery fails on the day

They are not commensurable on their own, so the ranking does two things rather
than one:

1.  **Pareto front.** A plan is marked optimal when nothing else beats it on one
    objective without losing on another. This is objective — it needs no weights
    and no preference — and it is what stops the UI presenting a plan that is
    simply worse than another plan in every respect.

2.  **Scalarised score**, as an auditable baseline and model-failure fallback.

Recommendation AI may personalize the ordering of validated eligible plans. Its
IDs are checked again in this module before they can replace the baseline order.

        score = money + time + churn + experience + fragility

    The time value is the interesting term. It is not a setting the member picks;
    it is regressed from what they actually chose the last time they had this
    trade-off in front of them. The same cancellation therefore ranks differently
    for two members with the same itinerary, which is the point.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from .catalog import PROFILES_BY_ID
from .domain import CURRENCY, Priority, RecoveryPlan, money, signed_money


# What a plan failing on the day is worth avoiding, before the member's own
# tolerance is applied. A missed connection on this trip means a second recovery
# from a worse position — in a foreign airport, at night, with the downstream
# bookings already amended.
RELIABILITY_WEIGHT = 150.0


@dataclass(frozen=True)
class Weights:
    """SGD per unit of each non-monetary objective."""

    id: str
    label: str
    time_value: float          # SGD per hour of trip given up
    switching_cost: float      # SGD per booking that has to be re-transacted
    description: str
    # 0 = will not accept a fragile plan at any price, 1 = indifferent to it.
    risk_tolerance: float = 0.5

    @property
    def reliability_weight(self) -> float:
        return RELIABILITY_WEIGHT * (1.0 - self.risk_tolerance)

    def public(self) -> Dict:
        return {
            "id": self.id,
            "label": self.label,
            "time_value": self.time_value,
            "switching_cost": self.switching_cost,
            "risk_tolerance": self.risk_tolerance,
            "reliability_weight": round(self.reliability_weight, 2),
            "description": self.description,
        }


PRESETS: Dict[str, Weights] = {
    Priority.COST.value: Weights(
        id="cost", label="Lowest cost", time_value=0.0, switching_cost=0.0,
        risk_tolerance=1.0,
        description="Money is the only thing that counts. Hours, rebookings and risk are free.",
    ),
    Priority.TIME.value: Weights(
        id="time", label="Earliest arrival", time_value=140.0, switching_cost=0.0,
        risk_tolerance=0.35,
        description="Get there soonest; the fare difference is secondary, and a blown connection is not fast.",
    ),
    Priority.DISRUPTION.value: Weights(
        id="disruption", label="Least disruption", time_value=25.0, switching_cost=180.0,
        risk_tolerance=0.15,
        description="Touch as few bookings as possible, and do not bet the trip on a tight connection.",
    ),
    Priority.BALANCED.value: Weights(
        id="balanced", label="Balanced", time_value=30.0, switching_cost=45.0,
        risk_tolerance=0.5,
        description="Weigh money, hours, churn and fragility against each other.",
    ),
}


def weights_for(priority: str, profile_id: str = "time") -> Weights:
    """``inferred`` resolves to the member's own regressed time value."""
    if priority == Priority.INFERRED.value:
        profile = PROFILES_BY_ID.get(profile_id) or PROFILES_BY_ID["time"]
        time_value = float(profile["weight"])
        # Tolerance for churn is not a second, independent setting. It falls out
        # of the same behaviour: a member who waits two days for a fare drop is
        # also a member who does not mind their hotel being rebooked, and one who
        # pays to save five hours does not want four suppliers touched either.
        # Both are read off the same history, so both move together.
        switching_cost = round(max(15.0, time_value * 1.3), 1)
        risk_tolerance = float(profile.get("risk_tolerance", 0.5))
        return Weights(
            id="inferred",
            label=f"Inferred from history — {profile['name'].lower()}",
            time_value=time_value,
            switching_cost=switching_cost,
            risk_tolerance=risk_tolerance,
            description=(
                f"{CURRENCY} {profile['weight']}/hour, {CURRENCY} {switching_cost:g} per booking "
                f"touched and a {risk_tolerance:.0%} tolerance for a plan that might fail on the "
                f"day — all regressed from choices this member already made "
                f"({profile['description']})."
            ),
        )
    return PRESETS.get(priority, PRESETS[Priority.BALANCED.value])


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score(plan: RecoveryPlan, weights: Weights) -> float:
    return (
        plan.metrics.cost_delta
        + plan.metrics.hours_lost * weights.time_value
        + plan.metrics.bookings_changed * weights.switching_cost
        + plan.metrics.experience_lost
        + plan.metrics.reliability_risk * weights.reliability_weight
    )


def breakdown(plan: RecoveryPlan, weights: Weights) -> str:
    time_term = plan.metrics.hours_lost * weights.time_value
    churn_term = plan.metrics.bookings_changed * weights.switching_cost
    parts = [f"Money {signed_money(plan.metrics.cost_delta)}"]
    if weights.time_value:
        parts.append(
            f"Time {plan.metrics.hours_lost:g}h × {CURRENCY} {weights.time_value:g}/hr = {money(time_term)}"
        )
    if weights.switching_cost:
        parts.append(
            f"Churn {plan.metrics.bookings_changed} × {CURRENCY} {weights.switching_cost:g} = {money(churn_term)}"
        )
    if plan.metrics.experience_lost:
        parts.append(f"Experience given up {money(plan.metrics.experience_lost)}")
    if weights.reliability_weight and plan.metrics.reliability_risk:
        parts.append(
            f"Fragility {plan.metrics.reliability_risk:.0%} × {CURRENCY} "
            f"{weights.reliability_weight:g} = {money(plan.metrics.reliability_risk * weights.reliability_weight)}"
        )
    return "  +  ".join(parts) + f"  =  {money(plan.score)}"


# ---------------------------------------------------------------------------
# Pareto front
# ---------------------------------------------------------------------------

def _objectives(plan: RecoveryPlan) -> Sequence[float]:
    """Experience given up is a fourth objective, not a rounding error.

    Without it, "refund the park passport" dominates "re-date the park passport"
    on every axis — cheaper, no slower, one fewer live booking — and the front
    fills up with plans that win by quietly deleting the trip.
    """
    return (
        plan.metrics.cost_delta,
        plan.metrics.hours_lost,
        float(plan.metrics.bookings_changed),
        plan.metrics.experience_lost,
        plan.metrics.reliability_risk,
    )


def _dominates(a: RecoveryPlan, b: RecoveryPlan) -> bool:
    """``a`` dominates ``b`` when it is no worse on every objective and strictly
    better on at least one."""
    left, right = _objectives(a), _objectives(b)
    return all(x <= y for x, y in zip(left, right)) and any(x < y for x, y in zip(left, right))


def mark_pareto(plans: Iterable[RecoveryPlan]) -> List[RecoveryPlan]:
    plans = list(plans)
    valid = [plan for plan in plans if plan.valid]
    for plan in plans:
        plan.pareto_optimal = plan.valid and not any(
            other is not plan and _dominates(other, plan) for other in valid
        )
    return plans


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def rank(plans: List[RecoveryPlan], priority: str, profile_id: str = "time") -> Dict:
    """Score, order and explain. Invalid plans are kept but sorted to the back —
    the member is allowed to see that an option exists and why it does not work."""
    weights = weights_for(priority, profile_id)

    for plan in plans:
        plan.score = round(score(plan, weights), 2)
        plan.score_breakdown = breakdown(plan, weights)

    mark_pareto(plans)
    ordered = sorted(plans, key=lambda p: (not p.valid, p.score))

    recommended = next((p for p in ordered if p.valid), ordered[0] if ordered else None)
    runner_up = next((p for p in ordered if p.valid and p is not recommended), None)

    # Imported here rather than at module scope: explain.py reads Weights from
    # this module, and a top-level import would close the cycle.
    from .explain import reason_codes

    profile = PROFILES_BY_ID.get(profile_id)

    return {
        "weights": weights.public(),
        "reason_codes": {plan.id: reason_codes(plan, weights, profile) for plan in plans},
        "presets": [w.public() for w in PRESETS.values()],
        "order": [plan.id for plan in ordered],
        "recommended_plan_id": recommended.id if recommended else None,
        "explanation": _explain(recommended, runner_up, weights),
        "formula": (
            "score = whole-trip money + (hours given up × time value) + "
            "(bookings changed × switching cost). Lower is better."
        ),
        "notification": _notification(recommended),
    }


def _explain(plan: Optional[RecoveryPlan], runner_up: Optional[RecoveryPlan], weights: Weights) -> str:
    if plan is None:
        return "No recovery plan satisfies the trip's hard dependencies."

    m = plan.metrics
    if m.cost_delta == 0:
        money_text = "breaks even across the whole trip"
    elif m.cost_delta < 0:
        money_text = f"returns {money(abs(m.cost_delta))} net across the whole trip"
    else:
        money_text = f"costs {money(m.cost_delta)} net across the whole trip"

    tail = ""
    if runner_up:
        gap = round(runner_up.score - plan.score)
        tail = (
            f" That is {money(gap)} better than {runner_up.name}, the next plan that satisfies "
            f"every hard dependency."
        )

    return (
        f"{plan.name} leads under “{weights.label}”. It {money_text}, gives up "
        f"{m.hours_lost:g} hours and re-transacts {m.bookings_changed} "
        f"booking{'' if m.bookings_changed == 1 else 's'}, scoring {money(plan.score)}.{tail}"
    )


def _notification(plan: Optional[RecoveryPlan]) -> str:
    if plan is None:
        return "Your flight was cancelled and no automatic recovery satisfies the rest of the trip. A specialist is being connected."
    m = plan.metrics
    if m.cost_delta > 0:
        clause = f"for {money(m.cost_delta)} more across the whole trip"
    elif m.cost_delta < 0:
        clause = f"returning {money(abs(m.cost_delta))} across the whole trip"
    else:
        clause = "at no net change to the trip"
    return (
        f"Your SIN→NRT flight was cancelled. We recommend {plan.name} — {clause}, "
        f"touching {m.bookings_changed} booking{'' if m.bookings_changed == 1 else 's'}. "
        "Every alternative is one tap away."
    )


def apply_personalized_ranking(
    plans: Sequence[RecoveryPlan],
    ranking: Dict,
    ai_result: Dict,
) -> Dict:
    """Apply a validated model order while preserving deterministic fallback.

    ``ai_agents`` performs the first strict schema and ID validation. This
    second gate sits beside the optimizer so a future caller cannot bypass plan
    validity or eligibility accidentally.
    """

    baseline_order = list(ranking.get("order", []))
    ranking["deterministic_order"] = baseline_order
    ranking["deterministic_recommended_plan_id"] = ranking.get("recommended_plan_id")
    ranking["deterministic_explanation"] = ranking.get("explanation")
    by_id = {plan.id: plan for plan in plans}
    eligible = {
        plan.id for plan in plans
        if plan.valid and plan.pareto_optimal
    } or {plan.id for plan in plans if plan.valid}
    ranking["eligible_plan_ids"] = [
        plan_id for plan_id in baseline_order if plan_id in eligible
    ]

    if ai_result.get("status") == "generated":
        requested = ai_result.get("ordered_plan_ids")
        recommended_id = ai_result.get("recommended_plan_id")
        valid_order = (
            isinstance(requested, list)
            and len(requested) == len(set(requested))
            and set(requested) == eligible
            and bool(requested)
            and requested[0] == recommended_id
        )
        if valid_order:
            ranking["order"] = [
                *requested,
                *(plan_id for plan_id in baseline_order if plan_id not in requested),
            ]
            ranking["recommended_plan_id"] = recommended_id
            ranking["explanation"] = ai_result.get("member_explanation") or ranking["explanation"]
            ranking["notification"] = _notification(by_id.get(recommended_id))
            ranking["recommendation_mode"] = "ai_personalized"
        else:
            ai_result = {
                **ai_result,
                "status": "failed",
                "error_code": "optimizer_validation_failed",
            }

    if ai_result.get("status") != "generated":
        ranking["recommendation_mode"] = "deterministic_fallback"
    ranking["ai"] = ai_result
    return ranking
