from fastapi.testclient import TestClient

from api.index import PLANS, app, guardrail_violations, net_impact, score_plans


client = TestClient(app)


def test_impact_calculation_uses_whole_trip_costs():
    plan = {"fare_delta": -400, "hotel_impact": 300, "car_impact": 0}

    assert net_impact(plan) == -100


def test_ranking_changes_by_customer_profile():
    time_recovery = score_plans("time")
    balanced_recovery = score_plans("balanced")
    cost_recovery = score_plans("cost")

    assert time_recovery["recommended_plan_id"] == "A"
    assert balanced_recovery["recommended_plan_id"] == "B"
    assert cost_recovery["recommended_plan_id"] == "C"


def test_guardrails_remove_invalid_candidates():
    invalid_plan = {
        "fare_delta": 100,
        "hotel_impact": 0,
        "car_impact": 0,
        "seat_available": False,
        "route_valid": False,
        "connection_valid": False,
        "arrival_too_late": True,
    }

    assert guardrail_violations(invalid_plan) == [
        "SEAT_UNAVAILABLE",
        "INVALID_ROUTE",
        "INVALID_CONNECTION",
        "ARRIVAL_TOO_LATE",
    ]


def test_recommendation_endpoint_returns_ranked_candidates():
    client.post("/api/disruptions", json={"trip_id": "TRIP-001", "type": "flight_cancelled"})
    response = client.post("/api/recommendations", json={"trip_id": "TRIP-001", "profile_id": "time"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["recommended_plan"]["id"] == "A"
    assert len(payload["alternatives"]) == 2
    assert payload["recovery"]["recommended"]["breakdown"]["reliability_penalty"] > 0
    assert "audit_id" in payload


def test_confirmation_updates_recovery_status_and_returns_steps():
    response = client.post(
        "/api/recovery/confirm",
        json={"trip_id": "TRIP-001", "candidate_id": "A", "profile_id": "time"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["confirmed"] is True
    assert payload["trip"]["status"] == "recovered"
    assert [step["state"] for step in payload["execution_steps"]] == ["SUCCESS"] * 5


def test_every_recovery_plan_exposes_verified_amex_partner_links():
    for plan in PLANS:
        links = [link for step in plan["journey"] for link in step.get("links", [])]

        assert links, f"Plan {plan['id']} should expose at least one partner link"
        assert all(link["url"].startswith("https://www.americanexpress.com/") for link in links)


def test_same_night_plan_uses_a_real_terminal_three_lounge():
    lounge_step = PLANS[0]["journey"][1]

    assert lounge_step["title"] == "SATS Premier Lounge, Terminal 3"
    assert "sats-premier-lounge-terminal3" in lounge_step["links"][0]["url"]
