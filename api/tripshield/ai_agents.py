"""Bounded model agents for personalized travel-recovery recommendations.

Connector facts and hard feasibility are prepared by deterministic server code.
Each specialist sees only its own tasks and option IDs through an immutable MCP
snapshot. The final agent may order only validated eligible plan IDs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import ai
from .mcp_server import create_role_scoped_mcp


SPECIALIST_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "task_id": {"type": "string"},
                    "ordered_option_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    },
                    "recommended_option_id": {"type": "string"},
                    "rationale": {"type": "string", "minLength": 1, "maxLength": 1200},
                    "risks": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 500},
                    },
                    "deprioritized_option_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    },
                },
                "required": [
                    "task_id",
                    "ordered_option_ids",
                    "recommended_option_id",
                    "rationale",
                    "risks",
                    "deprioritized_option_ids",
                ],
            },
        }
    },
    "required": ["assessments"],
}


RECOMMENDATION_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "recommended_plan_id": {"type": "string"},
        "ordered_plan_ids": {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
        },
        "ranking_rationale": {"type": "string", "minLength": 1, "maxLength": 2500},
        "member_explanation": {"type": "string", "minLength": 1, "maxLength": 1600},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "tradeoffs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "plan_id": {"type": "string"},
                    "label": {"type": "string", "minLength": 1, "maxLength": 100},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 600},
                },
                "required": ["plan_id", "label", "reason"],
            },
        },
        "referenced_plan_ids": {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
        },
        "referenced_option_ids": {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
        },
    },
    "required": [
        "recommended_plan_id",
        "ordered_plan_ids",
        "ranking_rationale",
        "member_explanation",
        "confidence",
        "tradeoffs",
        "referenced_plan_ids",
        "referenced_option_ids",
    ],
}


_SPECIALIST_PROMPT = """You are one bounded TripShield recovery specialist.
Call every available read-only tool exactly once. The inventory has already
passed hard server-side feasibility checks. Assess every option for the member,
return every option ID exactly once in preference order for each task, and name
the first as recommended. Do not invent identifiers or repeat supplier facts as
new facts. Do not request booking, cancellation, payment, web or HTTP access.
Return only the declared structured JSON object.
"""


_RECOMMENDATION_PROMPT = """You are TripShield's final Recommendation AI.
Call every available read-only tool exactly once. Recommend and order only the
eligible validated plan IDs supplied by the server. Personalize the decision
using member history and specialist evidence. Prices, times, feasibility,
metrics, provenance and eligibility are immutable. Do not invent identifiers,
offers or facts. Return only the declared structured JSON object.
"""


def _require_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("text is required")
    return value.strip()


def _require_unique_strings(value: Any) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("a string list is required")
    if len(set(value)) != len(value):
        raise ValueError("IDs must be unique")
    return list(value)


def _specialist_validator(
    raw: Dict[str, Any],
    *,
    option_ids_by_task: Mapping[str, Sequence[str]],
) -> Dict[str, Any]:
    if set(raw) != {"assessments"} or not isinstance(raw["assessments"], list):
        raise ValueError("invalid specialist object")
    expected_tasks = set(option_ids_by_task)
    seen_tasks: set[str] = set()
    validated: List[Dict[str, Any]] = []
    for assessment in raw["assessments"]:
        if not isinstance(assessment, Mapping) or set(assessment) != {
            "task_id",
            "ordered_option_ids",
            "recommended_option_id",
            "rationale",
            "risks",
            "deprioritized_option_ids",
        }:
            raise ValueError("invalid assessment")
        task_id = str(assessment["task_id"])
        if task_id not in expected_tasks or task_id in seen_tasks:
            raise ValueError("unknown or duplicate task")
        seen_tasks.add(task_id)
        expected_options = list(option_ids_by_task[task_id])
        ordered = _require_unique_strings(assessment["ordered_option_ids"])
        if set(ordered) != set(expected_options) or len(ordered) != len(expected_options):
            raise ValueError("option order must be an exact permutation")
        recommended = str(assessment["recommended_option_id"])
        if not ordered or recommended != ordered[0]:
            raise ValueError("recommended option must lead the order")
        risks = assessment["risks"]
        if not isinstance(risks, list) or not all(isinstance(item, str) for item in risks):
            raise ValueError("risks must be strings")
        deprioritized = _require_unique_strings(assessment["deprioritized_option_ids"])
        if not set(deprioritized).issubset(set(expected_options)):
            raise ValueError("unknown deprioritized option")
        validated.append({
            "task_id": task_id,
            "ordered_option_ids": ordered,
            "recommended_option_id": recommended,
            "rationale": _require_text(assessment["rationale"]),
            "risks": [item.strip() for item in risks if item.strip()],
            "deprioritized_option_ids": deprioritized,
        })
    if seen_tasks != expected_tasks:
        raise ValueError("every task must be assessed")
    return {"assessments": validated}


async def assess_specialty(
    *,
    specialty: str,
    agent_name: str,
    tasks: Sequence[Dict[str, Any]],
    options_by_task: Mapping[str, Sequence[Dict[str, Any]]],
    member_history: Dict[str, Any],
    safety_identifier: str,
    provider_client: Optional[Any] = None,
    mcp_client_factory: Optional[Any] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Run one batched specialist call over all tasks for one capability."""

    usable_tasks = [task for task in tasks if options_by_task.get(str(task.get("id")))]
    if not usable_tasks:
        return {
            "role": agent_name,
            "specialty": specialty,
            "status": "not_requested",
            "provider": None,
            "model": None,
            "transport": "in_process",
            "tools_used": [],
            "task_ids": [],
            "assessments": [],
            "latency_ms": 0,
            "error_code": None,
        }

    search_tool = f"search_{specialty}_inventory"
    safe_tasks = [
        {
            **task,
            "tools": ["get_recovery_tasks", search_tool, "get_member_choice_history"],
        }
        for task in usable_tasks
    ]
    task_ids = [str(task["id"]) for task in safe_tasks]
    inventory = {
        task_id: list(options_by_task[task_id])
        for task_id in task_ids
    }
    option_ids_by_task = {
        task_id: [str(option["id"]) for option in inventory[task_id]]
        for task_id in task_ids
    }
    bound = create_role_scoped_mcp(
        role=specialty,
        instructions=f"Read-only context for the {agent_name}.",
        tools={
            "get_recovery_tasks": (
                "Read the immutable recovery tasks and hard constraints for this specialty.",
                {"tasks": safe_tasks},
            ),
            search_tool: (
                "Search the request-cached, server-validated inventory for these tasks.",
                {"options_by_task": inventory},
            ),
            "get_member_choice_history": (
                "Read the immutable member preference profile and synthetic choice history.",
                member_history,
            ),
        },
    )
    run = await ai.run_structured_agent(
        role=agent_name,
        bound_mcp=bound,
        output_schema=SPECIALIST_OUTPUT_SCHEMA,
        schema_name=f"tripshield_{specialty}_assessment",
        instructions=_SPECIALIST_PROMPT,
        user_prompt=f"Assess all {specialty} recovery tasks for this member.",
        safety_identifier=safety_identifier,
        output_validator=lambda raw: _specialist_validator(
            raw, option_ids_by_task=option_ids_by_task
        ),
        provider_client=provider_client,
        mcp_client_factory=mcp_client_factory,
        env=env,
    )
    output = run.pop("output", None) or {}
    run.update({
        "specialty": specialty,
        "task_ids": task_ids,
        "assessments": output.get("assessments", []),
    })
    return run


def _recommendation_validator(
    raw: Dict[str, Any],
    *,
    eligible_plan_ids: Sequence[str],
    known_option_ids: Sequence[str],
) -> Dict[str, Any]:
    if set(raw) != set(RECOMMENDATION_OUTPUT_SCHEMA["required"]):
        raise ValueError("invalid recommendation object")
    eligible = list(eligible_plan_ids)
    ordered = _require_unique_strings(raw["ordered_plan_ids"])
    if set(ordered) != set(eligible) or len(ordered) != len(eligible):
        raise ValueError("plan order must be an exact eligible permutation")
    recommended = str(raw["recommended_plan_id"])
    if not ordered or recommended != ordered[0]:
        raise ValueError("recommended plan must lead the order")
    referenced_plans = _require_unique_strings(raw["referenced_plan_ids"])
    referenced_options = _require_unique_strings(raw["referenced_option_ids"])
    if recommended not in referenced_plans or not set(referenced_plans).issubset(set(eligible)):
        raise ValueError("unknown referenced plan")
    if not set(referenced_options).issubset(set(known_option_ids)):
        raise ValueError("unknown referenced option")
    confidence = raw["confidence"]
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise ValueError("confidence must be numeric")
    if not 0 <= float(confidence) <= 1:
        raise ValueError("confidence is outside the allowed range")
    tradeoffs = raw["tradeoffs"]
    if not isinstance(tradeoffs, list):
        raise ValueError("tradeoffs must be a list")
    clean_tradeoffs = []
    for item in tradeoffs:
        if not isinstance(item, Mapping) or set(item) != {"plan_id", "label", "reason"}:
            raise ValueError("invalid tradeoff")
        plan_id = str(item["plan_id"])
        if plan_id not in eligible:
            raise ValueError("unknown tradeoff plan")
        clean_tradeoffs.append({
            "plan_id": plan_id,
            "label": _require_text(item["label"]),
            "reason": _require_text(item["reason"]),
        })
    return {
        "recommended_plan_id": recommended,
        "ordered_plan_ids": ordered,
        "ranking_rationale": _require_text(raw["ranking_rationale"]),
        "member_explanation": _require_text(raw["member_explanation"]),
        "confidence": round(float(confidence), 3),
        "tradeoffs": clean_tradeoffs,
        "referenced_plan_ids": referenced_plans,
        "referenced_option_ids": referenced_options,
    }


async def recommend_plans(
    *,
    graph: Dict[str, Any],
    plans: Sequence[Dict[str, Any]],
    ranking: Dict[str, Any],
    member_history: Dict[str, Any],
    specialist_findings: Sequence[Dict[str, Any]],
    safety_identifier: str,
    provider_client: Optional[Any] = None,
    mcp_client_factory: Optional[Any] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Personalize the order of validated eligible plans, or fail closed."""

    eligible = [
        str(plan["id"])
        for plan in plans
        if plan.get("valid") and plan.get("pareto_optimal")
    ]
    if not eligible:
        eligible = [str(plan["id"]) for plan in plans if plan.get("valid")]
    baseline = [plan_id for plan_id in ranking.get("order", []) if plan_id in eligible]
    eligible = baseline or eligible
    fallback_id = eligible[0] if eligible else ranking.get("recommended_plan_id")
    if not eligible:
        return {
            "role": "Recommendation AI",
            "status": "not_requested",
            "provider": None,
            "model": None,
            "transport": "in_process",
            "tools_used": [],
            "recommended_plan_id": fallback_id,
            "ordered_plan_ids": [],
            "ranking_rationale": None,
            "member_explanation": None,
            "confidence": None,
            "tradeoffs": [],
            "referenced_plan_ids": [],
            "referenced_option_ids": [],
            "eligible_plan_ids": [],
            "latency_ms": 0,
            "error_code": "no_eligible_plans",
        }

    known_options = sorted({
        str(option_id)
        for plan in plans
        for option_id in (plan.get("selections") or {}).values()
    })
    public_findings = [
        {
            key: finding.get(key)
            for key in (
                "role", "specialty", "status", "provider", "model",
                "tools_used", "task_ids", "assessments", "latency_ms", "error_code",
            )
        }
        for finding in specialist_findings
    ]
    bound = create_role_scoped_mcp(
        role="recommendation",
        instructions="Read-only context for TripShield's final Recommendation AI.",
        tools={
            "get_trip_graph": (
                "Read the immutable trip graph, disruption and deterministic impact.",
                graph,
            ),
            "list_candidate_plans": (
                "Read validated plans, immutable metrics, eligibility and baseline order.",
                {"plans": list(plans), "ranking": ranking, "eligible_plan_ids": eligible},
            ),
            "get_member_choice_history": (
                "Read the immutable member preference profile and synthetic choice history.",
                member_history,
            ),
            "list_specialist_findings": (
                "Read validated findings and fallbacks from the five specialist agents.",
                {"agents": public_findings},
            ),
        },
    )
    run = await ai.run_structured_agent(
        role="Recommendation AI",
        bound_mcp=bound,
        output_schema=RECOMMENDATION_OUTPUT_SCHEMA,
        schema_name="tripshield_personalized_recommendation",
        instructions=_RECOMMENDATION_PROMPT,
        user_prompt="Personalize the eligible recovery plans for this member.",
        safety_identifier=safety_identifier,
        output_validator=lambda raw: _recommendation_validator(
            raw,
            eligible_plan_ids=eligible,
            known_option_ids=known_options,
        ),
        provider_client=provider_client,
        mcp_client_factory=mcp_client_factory,
        env=env,
    )
    output = run.pop("output", None) or {}
    run.update({
        "recommended_plan_id": output.get("recommended_plan_id", fallback_id),
        "ordered_plan_ids": output.get("ordered_plan_ids", []),
        "ranking_rationale": output.get("ranking_rationale"),
        "member_explanation": output.get("member_explanation"),
        "confidence": output.get("confidence"),
        "tradeoffs": output.get("tradeoffs", []),
        "referenced_plan_ids": output.get("referenced_plan_ids", []),
        "referenced_option_ids": output.get("referenced_option_ids", []),
        "eligible_plan_ids": eligible,
    })
    return run
