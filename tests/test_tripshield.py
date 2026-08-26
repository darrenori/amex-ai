"""Tests for the Travel Recovery Orchestrator.

The propagation logic in ``graph.py`` is where the subtle bugs live — the
difference between a booking that shifts and one that breaks is a few minutes of
arithmetic, and getting it wrong is invisible until a plan is approved. Most of
what follows pins that arithmetic down.

Every test here descends from one written against the earlier three-hardcoded-
plan model. The assertions changed because the model did; the questions did not:

    whole-trip impact, not fare impact          -> test_impact_is_whole_trip_*
    ranking moves with the member's history     -> test_ranking_changes_by_*
    guardrails remove invalid candidates        -> test_hard_dependency_*
    recommendations come back ranked            -> test_planning_endpoint_*
    confirmation reports its steps              -> test_execution_*
    every plan exposes verified Amex links      -> test_every_option_exposes_*
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from api.index import _options_from, _rehydrate, app
from api.tripshield import catalog, connectors, explain, optimizer, orchestrator, store
from api.tripshield.domain import BookingStatus, Severity
from api.tripshield.graph import Itinerary, affected, propagate, total_exposure


@pytest.fixture()
def itinerary() -> Itinerary:
    return Itinerary(catalog.build_bookings(), catalog.build_dependencies())


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_ping_is_a_minimal_liveness_probe(client):
    response = client.get("/api/ping")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.fixture()
def planned():
    """The full planning pass, populated exactly the way the API populates it.

    Deliberately goes through ``_options_from`` and ``_rehydrate`` rather than
    keeping the orchestrator's own objects: those two are where a plan's numbers
    could drift between what the member is shown and what is later approved, so
    the tests should exercise the same round trip the endpoints do.
    """
    session = store.reset("pytest")
    session.cancelled = ["bk_flight_out"]
    result = asyncio.run(orchestrator.plan(session.itinerary, session.cancelled))

    session.catalogue = {o.id: o for o in _options_from(result)}
    session.plans = {p["id"]: _rehydrate(session, p) for p in result["plans"]}
    session.last_ranking = optimizer.rank(list(session.plans.values()), "inferred", "time")
    return session, result


# ---------------------------------------------------------------------------
# Impact propagation
# ---------------------------------------------------------------------------

def test_cancellation_cascades_through_hard_edges(itinerary):
    """The cancelled flight must reach the Osaka hotel four edges downstream."""
    verdicts = propagate(itinerary, cancelled=["bk_flight_out"])

    assert verdicts["bk_flight_out"].status is BookingStatus.CANCELLED
    assert verdicts["bk_transfer_nex"].status is BookingStatus.BROKEN
    assert verdicts["bk_activity_tdl"].status is BookingStatus.BROKEN
    # Reached only via activity -> domestic flight -> Osaka hotel.
    assert verdicts["bk_hotel_osaka"].status is BookingStatus.BROKEN


def test_soft_edge_degrades_rather_than_invalidates(itinerary):
    """Missing dinner costs a deposit; it does not invalidate the trip."""
    verdicts = propagate(itinerary, cancelled=["bk_flight_out"])

    assert verdicts["bk_dining_tokyo"].status is BookingStatus.AT_RISK
    assert verdicts["bk_dining_tokyo"].status is not BookingStatus.BROKEN


def test_impact_is_whole_trip_not_just_the_fare(itinerary):
    """Doing nothing costs the non-refundable spend across every booking."""
    verdicts = propagate(itinerary, cancelled=["bk_flight_out"])
    exposure = total_exposure(verdicts)

    flight_only = itinerary.bookings["bk_flight_out"].amount - itinerary.bookings["bk_flight_out"].refundable
    assert exposure > flight_only
    # 400 flight + 300 hotel night + 180 deposit + 190 domestic fare.
    assert exposure == pytest.approx(1070.0)


def test_a_replacement_is_still_checked_against_its_dependencies(itinerary):
    """A proposed time is a proposal, not an exemption.

    The hotel option that says "keep it, note a late arrival" claims a check-in
    the member cannot make if they do not land until two days later. Accepting
    that claim uncritically is how an unworkable plan reaches approval.
    """
    late = connectors.INVENTORY_BY_ID["opt_flt_nh860"]
    verdicts = propagate(itinerary, replacements={"bk_flight_out": (late.start, late.end)})

    assert verdicts["bk_hotel_tokyo"].status is BookingStatus.BROKEN


def test_a_container_releases_the_member_at_check_in_not_check_out(itinerary):
    """A three-night stay must not look like it blocks the rest of the trip."""
    verdicts = propagate(itinerary, cancelled=[])
    # With nothing disrupted, the park entry the morning after check-in is fine
    # even though the hotel booking runs until the 21st.
    assert verdicts["bk_activity_tdl"].status is BookingStatus.UNAFFECTED


def test_dropping_a_booking_splices_the_graph_instead_of_cascading(itinerary):
    """Giving up the transfer does not mean giving up the hotel.

    The member still has to reach the property, so ``flight -> transfer -> hotel``
    must become ``flight -> hotel`` carrying both buffers — not a broken hotel.
    """
    spliced = itinerary.splice_out(["bk_transfer_nex"])

    assert "bk_transfer_nex" not in spliced.bookings
    edge = next(
        e for e in spliced.dependencies
        if e.source == "bk_flight_out" and e.target == "bk_hotel_tokyo"
    )
    assert edge.min_buffer == timedelta(minutes=105)   # 60 + 45
    assert edge.severity is Severity.HARD


def test_affected_lists_the_disrupted_booking_first(itinerary):
    verdicts = propagate(itinerary, cancelled=["bk_flight_out"])

    assert affected(verdicts)[0] == "bk_flight_out"


# ---------------------------------------------------------------------------
# Planning and ranking
# ---------------------------------------------------------------------------

def test_every_generated_plan_satisfies_the_hard_dependencies(planned):
    """The orchestrator must not offer a plan that cannot be executed."""
    _, result = planned

    assert result["plans"], "planning produced no candidates"
    for plan in result["plans"]:
        assert plan["valid"], f"{plan['name']} leaves a hard dependency unsatisfied"


def test_plans_are_whole_answers_covering_the_cancelled_leg(planned):
    _, result = planned

    for plan in result["plans"]:
        assert "bk_flight_out" in plan["selections"], f"{plan['name']} never replaces the cancelled flight"


def test_the_stated_objectives_resolve_to_different_answers(planned):
    """One cancellation, four objectives, more than one answer.

    Asserted on the *count* of distinct winners rather than on hardcoded plan
    ids, which would break every time a buffer or a fare is tuned.
    """
    session, _ = planned
    plans = list(session.plans.values())

    winners = {
        objective: optimizer.rank(plans, objective)["recommended_plan_id"]
        for objective in ("cost", "time", "disruption", "balanced")
    }

    assert len(set(winners.values())) >= 3, f"objectives collapsed onto one answer: {winners}"


def test_ranking_responds_to_the_inferred_history(planned):
    """The claim is that the member's own history moves the ranking — not that
    it always changes the winner.

    Once the whole trip is priced, the same-day rebooking is genuinely the right
    answer across all three synthetic histories: the cheap two-day fare only
    looks cheap until the forfeited nights and the abandoned park day are
    counted. The history still does real work, and it shows in where the cheap
    option lands rather than in whether it wins.
    """
    session, _ = planned
    plans = list(session.plans.values())
    by_id = {p.id: p for p in plans}

    # The cheapest fare on the board, which costs 46 hours and two experiences.
    thrifty = min(plans, key=lambda p: p.metrics.cost_delta)

    def rank_of(profile: str) -> int:
        order = optimizer.rank(plans, "inferred", profile)["order"]
        return order.index(thrifty.id)

    assert rank_of("cost") < rank_of("balanced") < rank_of("time"), (
        "a cost-sensitive history should rate the cheap, slow plan more highly "
        "than a time-sensitive one"
    )


def test_a_cost_sensitive_history_prices_an_hour_lower(planned):
    session, _ = planned
    plans = list(session.plans.values())
    thrifty = min(plans, key=lambda p: p.metrics.cost_delta)

    patient = optimizer.score(thrifty, optimizer.weights_for("inferred", "cost"))
    hurried = optimizer.score(thrifty, optimizer.weights_for("inferred", "time"))

    assert patient < hurried


def test_refunding_an_experience_is_not_treated_as_free(planned):
    """A plan that deletes the park day must not look strictly better than one
    that re-dates it just because the refund lands."""
    session, _ = planned
    catalogue = session.catalogue

    keep = orchestrator.materialize(
        session.itinerary, session.cancelled,
        {"bk_flight_out": "opt_flt_cx715", "bk_activity_tdl": "opt_act_move20"},
        catalogue, plan_id="keep",
    )
    refund = orchestrator.materialize(
        session.itinerary, session.cancelled,
        {"bk_flight_out": "opt_flt_cx715", "bk_activity_tdl": "opt_act_refund"},
        catalogue, plan_id="refund",
    )

    assert refund.metrics.cost_delta < keep.metrics.cost_delta      # cheaper in money
    assert refund.metrics.experience_lost > keep.metrics.experience_lost
    weights = optimizer.weights_for("balanced")
    assert optimizer.score(refund, weights) > optimizer.score(keep, weights)


def test_fragility_compounds_across_legs(planned):
    """A direct flight followed by the last train of the night is not a
    low-risk plan just because the flight is."""
    session, _ = planned

    safe = orchestrator.materialize(
        session.itinerary, session.cancelled,
        {"bk_flight_out": "opt_flt_sq12", "bk_transfer_nex": "opt_gnd_private"},
        session.catalogue, plan_id="safe",
    )
    fragile = orchestrator.materialize(
        session.itinerary, session.cancelled,
        {"bk_flight_out": "opt_flt_sq12", "bk_transfer_nex": "opt_gnd_nex_late"},
        session.catalogue, plan_id="fragile",
    )

    assert fragile.metrics.reliability_risk > safe.metrics.reliability_risk
    assert fragile.metrics.reliability_risk > connectors.INVENTORY_BY_ID["opt_flt_sq12"].reliability_risk


def test_pareto_front_excludes_dominated_plans(planned):
    session, _ = planned
    plans = list(session.plans.values())
    optimizer.rank(plans, "balanced")

    front = [p for p in plans if p.pareto_optimal]
    assert front, "nothing survived the Pareto filter"
    for candidate in plans:
        if candidate.pareto_optimal:
            continue
        assert any(optimizer._dominates(other, candidate) for other in plans)


# ---------------------------------------------------------------------------
# Hard-dependency guardrails
# ---------------------------------------------------------------------------

def test_hard_dependency_violations_block_a_hand_edited_plan(planned):
    """Taking the cheap next-morning flight and touching nothing else leaves
    five downstream bookings unsatisfiable."""
    session, _ = planned

    plan = orchestrator.materialize(
        session.itinerary, session.cancelled,
        {"bk_flight_out": "opt_flt_cx715"},
        session.catalogue, plan_id="edited", origin="edited",
    )

    assert not plan.valid
    hard = [v for v in plan.violations if v.severity is Severity.HARD]
    assert {v.booking_id for v in hard} >= {
        "bk_transfer_nex", "bk_hotel_tokyo", "bk_activity_tdl", "bk_hotel_osaka",
    }


def test_repairing_the_downstream_bookings_makes_the_plan_workable(planned):
    session, _ = planned

    plan = orchestrator.materialize(
        session.itinerary, session.cancelled,
        {
            "bk_flight_out": "opt_flt_cx715",
            "bk_hotel_tokyo": "opt_lod_drop1",
            "bk_activity_tdl": "opt_act_move20",
            "bk_transfer_nex": "opt_gnd_next_day",
            "bk_dining_tokyo": "opt_din_cancel",
        },
        session.catalogue, plan_id="repaired", origin="edited",
    )

    assert plan.valid, [v.message for v in plan.violations]


# ---------------------------------------------------------------------------
# Amex partner links
# ---------------------------------------------------------------------------

def test_every_option_exposes_verified_amex_partner_links():
    for item in connectors.INVENTORY:
        assert item.links, f"{item.id} should expose at least one partner link"
        for link in item.links:
            # Two Amex hosts serve this market: the global site and the
            # Singapore booking application. The second is not a subdomain of
            # the first, so it is named rather than assumed.
            assert link["url"].startswith((
                "https://www.americanexpress.com/",
                "https://travel.americanexpress.com.sg/",
            )), link
            assert link["label"].strip(), link


def test_the_same_day_rebooking_offers_a_real_changi_lounge():
    links = connectors.INVENTORY_BY_ID["opt_flt_sq12"].links

    assert any("sats-premier-lounge-terminal3" in link["url"] for link in links)


# ---------------------------------------------------------------------------
# Reason codes and audit
# ---------------------------------------------------------------------------

def test_reason_codes_describe_the_plan_not_the_recommendation(planned):
    session, _ = planned
    plans = list(session.plans.values())
    optimizer.rank(plans, "inferred", "time")
    weights = optimizer.weights_for("inferred", "time")
    profile = catalog.PROFILES_BY_ID["time"]

    for plan in plans:
        codes = explain.reason_codes(plan, weights, profile)
        assert ("PARETO_OPTIMAL" in codes) == plan.pareto_optimal
        assert ("EXPERIENCE_PRESERVED" in codes) == (plan.metrics.experience_lost == 0)
        assert ("HARD_DEPENDENCY_UNSATISFIED" in codes) == (not plan.valid)


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------

def test_detection_finds_the_cancellation_without_being_told(client):
    response = client.post("/api/disruption/detect")

    assert response.status_code == 200
    payload = response.json()
    assert [d["booking_id"] for d in payload["disruptions"]] == ["bk_flight_out"]
    # AeroDataBox's own vocabulary, one l.
    assert payload["checks"][0]["status"] == "Canceled"
    assert payload["checks"][1]["disruptive"] is False


def test_planning_endpoint_returns_ranked_candidates_and_a_trace(client):
    client.post("/api/disruption/detect")
    response = client.post("/api/recovery/plan", json={"priority": "inferred", "profile_id": "time"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ranking"]["recommended_plan_id"] in {p["id"] for p in payload["plans"]}
    assert payload["ranking"]["order"][0] == payload["ranking"]["recommended_plan_id"]
    assert payload["tasks"] and payload["options"] and payload["agents"]
    assert payload["ranking"]["reason_codes"]


def test_validation_is_server_side_and_can_refuse_a_plan(client):
    client.post("/api/disruption/detect")
    client.post("/api/recovery/plan", json={})

    response = client.post(
        "/api/recovery/plan/validate",
        json={"selections": {"bk_flight_out": "opt_flt_cx715"}},
    )

    assert response.status_code == 200
    plan = response.json()["plan"]
    assert plan["valid"] is False
    assert any(v["severity"] == "hard" for v in plan["violations"])


def test_an_unworkable_plan_cannot_be_approved(client):
    client.post("/api/disruption/detect")
    client.post("/api/recovery/plan", json={})
    broken = client.post(
        "/api/recovery/plan/validate",
        json={"selections": {"bk_flight_out": "opt_flt_cx715"}},
    ).json()["plan"]

    response = client.post("/api/recovery/plan/approve", json={"plan_id": broken["id"]})

    assert response.status_code == 422


def test_execution_commits_one_step_at_a_time_and_gates_each_charge(client):
    client.post("/api/disruption/detect")
    plan = client.post("/api/recovery/plan", json={})
    recommended = plan.json()["ranking"]["recommended_plan_id"]
    run = client.post("/api/recovery/plan/approve", json={"plan_id": recommended}).json()["run"]

    assert run["state"] == "approved"
    assert all(step["state"] == "pending" for step in run["steps"])

    # A step that costs money stops for authorisation rather than committing.
    first = client.post(f"/api/execution/{run['id']}/advance", json={}).json()
    paid = first["run"]["steps"][0]["requires_payment"]
    assert first["result"]["reason"] == ("awaiting_payment" if paid else "done")

    for _ in range(20):
        current = client.post(
            f"/api/execution/{run['id']}/advance", json={"approve_payment": True}
        ).json()
        if current["run"]["state"] in {"complete", "failed"}:
            break

    assert current["run"]["state"] == "complete"
    assert current["run"]["progress"] == 1.0


def test_rollback_is_quoted_before_it_is_performed(client):
    client.post("/api/disruption/detect")
    plan = client.post("/api/recovery/plan", json={})
    recommended = plan.json()["ranking"]["recommended_plan_id"]
    run_id = client.post("/api/recovery/plan/approve", json={"plan_id": recommended}).json()["run"]["id"]

    client.post(f"/api/execution/{run_id}/advance", json={})
    client.post(f"/api/execution/{run_id}/advance", json={"approve_payment": True})

    quote = client.get(f"/api/execution/{run_id}/rollback-quote").json()["quote"]
    assert quote["committed"], "nothing was committed, so the quote proves nothing"
    # An airline refunds net of its fee, so the quote must not claim it is free.
    assert quote["unrecoverable_total"] > 0
    assert quote["fully_reversible"] is False

    result = client.post(f"/api/execution/{run_id}/cancel", json={"rollback": True}).json()
    assert result["run"]["state"] == "rolled_back"
    assert result["refunded"] == pytest.approx(quote["refundable_total"])


def test_stopping_a_run_leaves_committed_steps_alone(client):
    client.post("/api/disruption/detect")
    plan = client.post("/api/recovery/plan", json={})
    recommended = plan.json()["ranking"]["recommended_plan_id"]
    run_id = client.post("/api/recovery/plan/approve", json={"plan_id": recommended}).json()["run"]["id"]

    client.post(f"/api/execution/{run_id}/advance", json={})
    client.post(f"/api/execution/{run_id}/advance", json={"approve_payment": True})
    result = client.post(f"/api/execution/{run_id}/cancel", json={"rollback": False}).json()

    run = result["run"]
    assert run["state"] == "cancelled"
    assert result["refunded"] == 0.0
    assert any(step["state"] == "done" for step in run["steps"])
    assert any(step["state"] == "skipped" for step in run["steps"])
    # A run stopped one step in is a third of the way through, not finished.
    assert 0.0 < run["progress"] < 1.0


def test_approval_writes_an_audit_record_with_model_versions(client):
    client.post("/api/disruption/detect")
    plan = client.post("/api/recovery/plan", json={})
    recommended = plan.json()["ranking"]["recommended_plan_id"]

    approved = client.post("/api/recovery/plan/approve", json={"plan_id": recommended}).json()
    record = approved["audit"]

    assert record["followed_recommendation"] is True
    assert record["model_versions"] == explain.MODEL_VERSIONS
    assert record["scores"] and record["reason_codes"]
    assert client.get("/api/audit").json()["records"][-1]["id"] == record["id"]


def test_a_missing_run_is_reported_not_invented(client):
    """Runs record transactions that actually happened, so a cache miss must
    never be papered over with a freshly seeded one."""
    assert client.get("/api/execution/run_does_not_exist").status_code == 410


def test_connector_report_names_real_upstreams(client):
    payload = client.get("/api/connectors").json()
    upstreams = {c["upstream"] for c in payload["connectors"]}

    assert {"AeroDataBox", "Duffel", "LiteAPI (Nuitée)", "Viator Partner API"} <= upstreams
    for connector in payload["connectors"]:
        assert connector["tools"], connector["server"]
        assert connector["docs"].startswith("https://")


def test_strict_schemas_carry_no_keywords_openai_rejects():
    """OpenAI's strict structured-output mode rejects a schema containing
    ``uniqueItems`` with a 400 before the call is ever costed, which silently
    failed every agent round. Uniqueness is enforced in Python at parse time
    (``_validated_output`` / ``_require_unique_strings``), so the keyword must
    not creep back into a schema that is sent with ``strict: True``."""
    from api.tripshield.ai import AI_OUTPUT_SCHEMA
    from api.tripshield.ai_agents import (
        RECOMMENDATION_OUTPUT_SCHEMA,
        SPECIALIST_OUTPUT_SCHEMA,
    )

    banned = {"uniqueItems", "minItems", "maxItems", "pattern", "format"}

    def walk(node, path="root"):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in banned, f"{path}: '{key}' is not permitted in strict mode"
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    for name, schema in [
        ("AI_OUTPUT_SCHEMA", AI_OUTPUT_SCHEMA),
        ("SPECIALIST_OUTPUT_SCHEMA", SPECIALIST_OUTPUT_SCHEMA),
        ("RECOMMENDATION_OUTPUT_SCHEMA", RECOMMENDATION_OUTPUT_SCHEMA),
    ]:
        walk(schema, name)


def test_openai_tool_schemas_are_normalised_for_strict_mode():
    """When the MCP SDK is installed, FastMCP derives each tool's schema from
    the Python signature and emits ``{"type": "object", "properties": {},
    "title": "...Arguments"}`` — no ``additionalProperties``. OpenAI's strict
    function calling rejects that with 400 invalid_function_parameters before
    the call is costed, which failed every agent round regardless of billing.
    The provider adapter must normalise it."""
    from api.tripshield.ai import _openai_tools, _strict_schema

    fastmcp_shape = {
        "name": "get_recovery_tasks",
        "description": "Read the immutable recovery tasks.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "title": "get_recovery_tasksArguments",
        },
    }
    [tool] = _openai_tools([fastmcp_shape])
    assert tool["strict"] is True
    assert tool["parameters"]["additionalProperties"] is False
    assert "title" not in tool["parameters"]

    nested = _strict_schema({
        "type": "object",
        "title": "drop me",
        "properties": {
            "outer": {
                "type": "object",
                "properties": {"inner": {"type": "string"}},
            },
            "list": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "object", "properties": {"leaf": {"type": "string"}}},
            },
        },
    })

    def every_object(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(node.get("properties", {}))
            assert "title" not in node and "uniqueItems" not in node
            for value in node.values():
                every_object(value)
        elif isinstance(node, list):
            for value in node:
                every_object(value)

    every_object(nested)


def test_agent_snapshots_omit_plumbing_the_model_must_not_act_on():
    """Every field crossing the MCP boundary is paid for in tokens on each
    agent call. Provenance, adapter calls and offer ids are server-side
    concerns the agent is forbidden to act on, so they must not be sent."""
    from api.tripshield.ai_agents import _lean_option, _lean_plan, _lean_graph

    lean = _lean_option({
        "id": "opt_x", "task_id": "task_x", "title": "Keep it",
        "detail": "d" * 200, "cost_delta": 0.0, "quality": 0.9,
        "tool_call": "Book the room", "tool_endpoint": "rest/liteapi · POST /rates/book",
        "source_upstream": "LiteAPI", "source_mode": "fixture", "source_note": "x",
        "supplier_offer_id": "rate_9QR", "connector": "lodging", "synthetic": True,
    })
    for leaked in (
        "tool_call", "tool_endpoint", "source_upstream", "source_mode",
        "source_note", "supplier_offer_id", "connector", "synthetic",
    ):
        assert leaked not in lean, leaked
    assert lean["id"] == "opt_x" and lean["cost_delta"] == 0.0
    assert len(lean["detail"]) <= 90

    assert "score_breakdown" not in _lean_plan({
        "id": "plan_01", "score": 1.0, "score_breakdown": "Money -SGD 145 + ...",
    })

    graph = _lean_graph({
        "nodes": [{"id": "bk", "label": "L", "status": "broken", "amount": 1.0,
                   "supplier_ref": "ord_secret", "note": "n" * 300, "meta": {"a": 1}}],
        "edges": [{"source": "a", "target": "b", "severity": "hard",
                   "rationale": "r" * 300}],
        "assessment": {"verdicts": {"bk": {"status": "broken", "slack_minutes": -5,
                                           "reason": "z" * 300}}, "affected": ["bk"]},
    })
    assert "supplier_ref" not in graph["nodes"][0] and "note" not in graph["nodes"][0]
    assert "rationale" not in graph["edges"][0]
    assert "reason" not in graph["assessment"]["verdicts"]["bk"]
    assert graph["assessment"]["verdicts"]["bk"]["status"] == "broken"


def test_openai_client_can_target_a_free_compatible_endpoint():
    """OpenAI has no free tier, but Gemini, Groq, OpenRouter and Ollama all
    speak its protocol. Redirecting the base URL is what makes a free provider
    usable without touching the tool-calling or strict-schema paths."""
    import os
    from api.tripshield.ai import _provider_client

    previous = os.environ.get("OPENAI_BASE_URL")
    try:
        os.environ["OPENAI_BASE_URL"] = "https://api.groq.com/openai/v1"
        client = _provider_client("openai", "sk-test", 5.0)
        assert "groq.com" in str(client.base_url)
        # A quota 429 can never succeed on retry; the fallback is the safety net.
        assert client.max_retries == 0

        os.environ.pop("OPENAI_BASE_URL")
        assert "api.openai.com" in str(_provider_client("openai", "sk-test", 5.0).base_url)
    finally:
        if previous is None:
            os.environ.pop("OPENAI_BASE_URL", None)
        else:
            os.environ["OPENAI_BASE_URL"] = previous


def test_every_shipped_link_is_verified():
    """Two URLs shipped here returned 404: a Love Dining path that did not
    exist and a Fine Hotels + Resorts path that is not used in this market.
    Both looked plausible, which is exactly the problem. Every destination the
    app can send a member to must be one that was actually requested and
    answered, so the set below is the allowlist and anything new has to be
    checked before it joins it."""
    from api.tripshield import connectors

    verified = {
        # Checked 2026-08-25, each returned 200.
        "https://travel.americanexpress.com.sg/shopping/",
        "https://www.americanexpress.com/en-sg/travel/discover/property-results/c/27/dt/4/d/Tokyo%2CJapan",
        "https://www.americanexpress.com/en-sg/travel/discover/property-results/c/27/dt/4/d/Osaka%2CJapan",
        "https://www.americanexpress.com/en-sg/travel/discover/property-results/c/27/dt/4/d/Singapore",
        "https://www.americanexpress.com/sg/benefits/love-dining/love-dining-hotels.html",
        "https://www.americanexpress.com/en-sg/travel/hotels/",
        "https://www.americanexpress.com/en-sg/travel/lounges/the-platinum-card/SIN/sats-premier-lounge-terminal3-RL47nYNPjn/",
        "https://www.americanexpress.com/en-sg/travel/cars/",
        "https://www.americanexpress.com/en-sg/travel/discover/property-results/c/27/dt/4/d/Tokyo",
        "https://www.americanexpress.com/sg/benefits/love-dining/love-restaurants.html",
    }

    shipped = set()
    for item in connectors.INVENTORY:
        for link in list(item.links) + connectors.option_links(item):
            shipped.add(link["url"])

    unverified = sorted(shipped - verified)
    assert not unverified, (
        "these destinations are not in the verified allowlist; request each one "
        f"and add it only if it answers: {unverified}"
    )
