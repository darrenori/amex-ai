"""Contracts for the bounded multi-agent recommendation workflow."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from api.index import app
from api.tripshield import ai_agents, catalog, optimizer, orchestrator
from api.tripshield.domain import PlanMetrics, RecoveryPlan
from api.tripshield.graph import Itinerary
from api.tripshield.mcp_server import create_role_scoped_mcp


def test_role_scoped_mcp_is_immutable_and_has_no_transaction_tools():
    source = {"tasks": [{"id": "task_1"}]}
    bound = create_role_scoped_mcp(
        role="activity",
        instructions="Read only.",
        tools={
            "get_recovery_tasks": ("Read tasks.", source),
            "search_activity_inventory": ("Read inventory.", {"options": []}),
        },
    )

    source["tasks"][0]["id"] = "mutated"
    first = bound.call_local_tool("get_recovery_tasks")
    first["tasks"][0]["id"] = "also_mutated"

    assert bound.call_local_tool("get_recovery_tasks")["tasks"][0]["id"] == "task_1"
    assert bound.tool_names == ("get_recovery_tasks", "search_activity_inventory")
    assert not any(
        forbidden in name
        for name in bound.tool_names
        for forbidden in ("book", "cancel", "pay", "approve", "http", "scrape")
    )


def test_specialist_validator_rejects_an_invented_option_id():
    raw = {
        "assessments": [{
            "task_id": "task_1",
            "ordered_option_ids": ["opt_real", "opt_invented"],
            "recommended_option_id": "opt_real",
            "rationale": "Best fit.",
            "risks": [],
            "deprioritized_option_ids": [],
        }]
    }

    with pytest.raises(ValueError):
        ai_agents._specialist_validator(
            raw,
            option_ids_by_task={"task_1": ["opt_real", "opt_other"]},
        )


def test_recommendation_validator_rejects_an_ineligible_plan():
    raw = {
        "recommended_plan_id": "plan_invalid",
        "ordered_plan_ids": ["plan_invalid", "plan_valid"],
        "ranking_rationale": "Invalid choice.",
        "member_explanation": "Invalid choice.",
        "confidence": 0.7,
        "tradeoffs": [],
        "referenced_plan_ids": ["plan_invalid"],
        "referenced_option_ids": [],
    }

    with pytest.raises(ValueError):
        ai_agents._recommendation_validator(
            raw,
            eligible_plan_ids=["plan_valid"],
            known_option_ids=[],
        )


def test_optimizer_accepts_a_valid_personalized_order():
    def plan(plan_id: str, cost: float, hours: float) -> RecoveryPlan:
        return RecoveryPlan(
            id=plan_id,
            version=1,
            name=plan_id,
            strategy="test",
            summary="test",
            selections={"bk_flight": f"opt_{plan_id}"},
            metrics=PlanMetrics(
                cost_delta=cost,
                hours_lost=hours,
                bookings_changed=1,
                forfeited=0,
                refund_expected=0,
                arrival=None,
            ),
            pareto_optimal=True,
        )

    plans = [plan("plan_time", 200, 1), plan("plan_cost", 20, 6)]
    ranking = optimizer.rank(plans, "time", "time")
    result = optimizer.apply_personalized_ranking(plans, ranking, {
        "status": "generated",
        "recommended_plan_id": "plan_cost",
        "ordered_plan_ids": ["plan_cost", "plan_time"],
        "member_explanation": "This member accepts the delay to save more.",
    })

    assert result["recommended_plan_id"] == "plan_cost"
    assert result["order"][:2] == ["plan_cost", "plan_time"]
    assert result["recommendation_mode"] == "ai_personalized"


def test_orchestrator_runs_flight_first_then_downstream_specialists_concurrently(monkeypatch):
    itinerary = Itinerary(catalog.build_bookings(), catalog.build_dependencies())
    events = []
    active = 0
    maximum_active = 0

    async def fake_assess(**kwargs):
        nonlocal active, maximum_active
        specialty = kwargs["specialty"]
        events.append(f"start:{specialty}")
        if specialty != "flights":
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.02)
            active -= 1
        assessments = []
        for task in kwargs["tasks"]:
            task_id = task["id"]
            ids = [option["id"] for option in kwargs["options_by_task"].get(task_id, [])]
            if ids:
                assessments.append({
                    "task_id": task_id,
                    "ordered_option_ids": ids,
                    "recommended_option_id": ids[0],
                    "rationale": "Validated test preference.",
                    "risks": [],
                    "deprioritized_option_ids": [],
                })
        events.append(f"end:{specialty}")
        return {
            "role": kwargs["agent_name"],
            "specialty": specialty,
            "status": "generated" if assessments else "not_requested",
            "provider": "test",
            "model": "test-model",
            "transport": "in_process",
            "tools_used": [],
            "task_ids": [task["id"] for task in kwargs["tasks"]],
            "assessments": assessments,
            "latency_ms": 20,
            "error_code": None,
        }

    monkeypatch.setattr(ai_agents, "assess_specialty", fake_assess)
    result = asyncio.run(orchestrator.plan(
        itinerary,
        ["bk_flight_out"],
        member_history=catalog.PROFILES_BY_ID["time"],
        safety_identifier="pytest",
    ))

    assert events[0] == "start:flights"
    assert events[1] == "end:flights"
    assert maximum_active >= 2
    assert len(result["agent_runs"]) == 5
    assert any(plan["strategy"] == "personalized" for plan in result["plans"])


def test_planning_api_is_honest_when_no_model_provider_is_configured(monkeypatch):
    for key in (
        "AI_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "openai_api_key",
        "ANTHROPIC_MODEL", "OPENAI_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    client = TestClient(app)
    session_id = "pytest_ai_fallback"
    client.post(f"/api/session/reset?session_id={session_id}")
    detected = client.post(f"/api/disruption/detect?session_id={session_id}")
    assert detected.status_code == 200
    response = client.post("/api/recovery/plan", json={
        "session_id": session_id,
        "priority": "inferred",
        "profile_id": "time",
    })

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["agent_runs"]) == 6
    assert payload["ranking"]["recommendation_mode"] == "deterministic_fallback"
    assert payload["ranking"]["ai"]["status"] == "disabled"
    assert "pareto" not in payload["ranking"]["explanation"].lower()
