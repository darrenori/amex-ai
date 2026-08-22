import asyncio
import json
import os

import pytest
from api.tripshield import connectors
from api.tripshield.amex_partners import LAST_VERIFIED_AT, match_partner


def _duffel_offer(identifier, amount, *, currency="SGD", stops=0):
    segments = [{
        "departing_at": "2026-09-18T13:45:00+08:00",
        "arriving_at": "2026-09-18T21:50:00+09:00" if not stops else "2026-09-18T17:00:00+08:00",
        "marketing_carrier": {"iata_code": "SQ", "name": "Singapore Airlines"},
        "marketing_carrier_flight_number": "12",
    }]
    if stops:
        segments.append({
            "departing_at": "2026-09-18T19:00:00+08:00",
            "arriving_at": "2026-09-19T01:00:00+09:00",
            "marketing_carrier": {"iata_code": "SQ", "name": "Singapore Airlines"},
            "marketing_carrier_flight_number": "22",
        })
    return {
        "id": identifier, "total_amount": str(amount), "total_currency": currency,
        "expires_at": "2030-01-01T00:00:00Z", "cabin_class": "business",
        "owner": {"name": "Singapore Airlines"}, "slices": [{"segments": segments}],
    }


def test_connector_report_is_honest_about_reads_and_transactions(monkeypatch):
    monkeypatch.setenv("AERODATABOX_API_KEY", "aero")
    monkeypatch.setenv("DUFFEL_ACCESS_TOKEN", "duffel")
    monkeypatch.setenv("LITEAPI_SANDBOX_KEY", "lite")
    report = {item["key"]: item for item in connectors.connector_report()}

    assert report["status"]["mode"] == "live"
    assert report["flights"]["mode"] == report["lodging"]["mode"] == "sandbox"
    assert all(item["capabilities"]["transaction"] == "fixture" for item in report.values())
    assert report["activities"]["availability"] == "approval_required"
    assert report["dining"]["availability"] == "designed_fixture"
    assert report["ground"]["availability"] == "no_public_api"
    assert ".example" not in report["ground"]["base_url"]
    assert report["status"]["adapter"] == "rest/aerodatabox"
    assert report["flights"]["adapter"] == "rest/duffel"
    assert report["dining"]["adapter"] == "fixture/dining"
    assert all(item["server"].startswith("mcp/") for item in report.values())
    assert all(item["fallback_data"] == {
        "available": True,
        "type": "synthetic_dummy",
        "deterministic": True,
        "runtime_scraping": False,
    } for item in report.values())


def test_fixture_inventory_is_explicitly_labelled_synthetic(monkeypatch):
    monkeypatch.delenv("DUFFEL_ACCESS_TOKEN", raising=False)
    result = asyncio.run(connectors.search_live_or_fixture("flights", "bk_flight_out"))

    assert result.mode == "fixture"
    assert result.items
    assert all(item.synthetic is True and item.source_mode == "fixture" for item in result.items)


def test_amex_matching_is_exact_or_explicit_alias_only():
    match = match_partner("Hilton Tokyo Bay Hotel", category="lodging", market="JP")
    assert match and match["name"] == "Hilton Tokyo Bay"
    assert match["last_verified_at"] == LAST_VERIFIED_AT == "2026-08-22"
    assert match_partner("Hilton Tokyo", category="lodging", market="JP") is None
    assert match_partner("Hilton Tokyo Bay", category="lodging", market="SG") is None
    assert connectors.INVENTORY_BY_ID["opt_lod_keep"].amex_partner == match_partner(
        "Hilton Tokyo Bay", category="lodging", market="JP"
    )


def test_duffel_sandbox_search_normalizes_sgd_and_is_request_cached(monkeypatch):
    monkeypatch.setenv("DUFFEL_ACCESS_TOKEN", "test-token")
    calls = []

    def transport(request, timeout):
        calls.append((request, timeout))
        body = json.loads(request.data.decode())
        assert body["data"]["slices"][0] == {
            "origin": "SIN", "destination": "NRT", "departure_date": "2026-09-18"
        }
        return {"data": {"offers": [
            _duffel_offer("off_live_1", 3000),
            _duffel_offer("off_live_2", 2900, stops=1),
            _duffel_offer("off_wrong_fx", 50, currency="USD"),
        ]}}

    cache = {}
    first = asyncio.run(connectors.search_live_or_fixture(
        "flights", "bk_flight_out", cache=cache, transport=transport
    ))
    first.items.clear()
    second = asyncio.run(connectors.search_live_or_fixture(
        "flights", "bk_flight_out", cache=cache, transport=transport
    ))

    assert first.mode == second.mode == "sandbox"
    assert len(second.items) == 2
    assert len(calls) == 1
    assert second.items[0].source_mode == "sandbox"
    assert second.items[0].cost_delta == 200.0
    assert second.items[1].reliability_risk > second.items[0].reliability_risk


def test_duffel_falls_back_to_complete_fixture_set_when_too_few(monkeypatch):
    monkeypatch.setenv("DUFFEL_ACCESS_TOKEN", "test-token")
    result = asyncio.run(connectors.search_live_or_fixture(
        "flights", "bk_flight_out",
        transport=lambda request, timeout: {"data": {"offers": [_duffel_offer("only", 3000)]}},
    ))
    assert result.mode == "fixture"
    assert result.fallback_reason == "insufficient_eligible_offers"
    assert {item.id for item in result.items} == {
        "opt_flt_sq12", "opt_flt_nh802", "opt_flt_cx715", "opt_flt_nh860"
    }


def test_liteapi_sandbox_search_normalizes_partner_and_rejects_non_sgd(monkeypatch):
    monkeypatch.setenv("LITEAPI_SANDBOX_KEY", "test-key")

    def transport(request, timeout):
        body = json.loads(request.data.decode())
        assert body["currency"] == "SGD"
        assert body["guestNationality"] == "SG"
        return {"data": [{
            "hotelId": "hotel-hilton", "name": "Hilton Tokyo Bay", "starRating": 5,
            "roomTypes": [{"name": "King room", "rates": [
                {"rateId": "sgd-rate", "total": "1000.00", "currency": "SGD"},
                {"rateId": "usd-rate", "total": "1.00", "currency": "USD"},
            ]}],
        }]}

    result = asyncio.run(connectors.search_live_or_fixture(
        "lodging", "bk_hotel_tokyo", transport=transport
    ))
    assert result.mode == "sandbox"
    assert len(result.items) == 1
    item = result.items[0]
    assert item.cost_delta == 400.0
    assert item.quality == 1.0
    assert item.amex_partner["name"] == "Hilton Tokyo Bay"


def test_network_errors_fall_back_without_leaking_error_details(monkeypatch):
    monkeypatch.setenv("DUFFEL_ACCESS_TOKEN", "secret")

    def broken(request, timeout):
        raise TimeoutError("secret token and provider body")

    result = asyncio.run(connectors.search_live_or_fixture(
        "flights", "bk_flight_out", transport=broken
    ))
    assert result.mode == "fixture"
    assert result.fallback_reason == "upstream_timeouterror"
    assert "secret" not in result.fallback_reason


def test_item_snapshot_round_trip_and_execution_stays_fixture(monkeypatch):
    monkeypatch.setenv("DUFFEL_ACCESS_TOKEN", "test-token")
    item = connectors.INVENTORY_BY_ID["opt_flt_sq12"]
    restored = connectors.inventory_item_from_snapshot(connectors.inventory_item_snapshot(item))
    assert restored == item
    receipt = connectors.execute(restored, idempotency_key="test")
    assert receipt["mode"] == "fixture"


def test_aerodatabox_transport_is_injectable(monkeypatch):
    monkeypatch.setenv("AERODATABOX_API_KEY", "test-key")
    result = connectors.fetch_flight_status(
        "SQ638", "2026-09-18",
        transport=lambda request, timeout: [{"number": "SQ 638", "status": "Delayed"}],
    )
    assert result["source"] == "live"
    assert result["status"] == "Delayed"
    assert result["cancelled"] is False


def test_connector_health_returns_sanitized_bounded_shape(monkeypatch):
    monkeypatch.delenv("DUFFEL_ACCESS_TOKEN", raising=False)
    health = asyncio.run(connectors.connector_health("flights"))
    assert set(health) == {"checked_at", "checks"}
    assert health["checks"][0]["key"] == "flights"
    assert health["checks"][0]["error_category"] == "missing_credential"
    assert health["checks"][0]["candidate_count"] == 4


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1" or not os.environ.get("AERODATABOX_API_KEY"),
    reason="set RUN_LIVE_TESTS=1 and AERODATABOX_API_KEY",
)
def test_live_aerodatabox_read_smoke():
    result = connectors.fetch_flight_status("SQ638", "2026-09-18")
    assert result["source"] == "live"


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1" or not os.environ.get("DUFFEL_ACCESS_TOKEN"),
    reason="set RUN_LIVE_TESTS=1 and DUFFEL_ACCESS_TOKEN",
)
def test_live_duffel_test_offer_smoke():
    result = asyncio.run(connectors.search_live_or_fixture("flights", "bk_flight_out"))
    assert result.mode == "sandbox", result.fallback_reason
    assert len(result.items) >= 2


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1"
    or not os.environ.get("LITEAPI_SANDBOX_KEY")
    or not os.environ.get("LITEAPI_HOTEL_IDS"),
    reason="set RUN_LIVE_TESTS=1, LITEAPI_SANDBOX_KEY, and LITEAPI_HOTEL_IDS",
)
def test_live_liteapi_sandbox_rate_smoke():
    result = asyncio.run(connectors.search_live_or_fixture("lodging", "bk_hotel_tokyo"))
    assert result.mode == "sandbox", result.fallback_reason
    assert result.items
