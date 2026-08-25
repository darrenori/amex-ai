"""Direct REST adapters onto real travel APIs, with deterministic fallbacks.

Each connector is the standardized interface an agent gets to one external
capability. They are declared against real, currently-available products, and
every tool name below maps to a real endpoint on that product:

``status``      AeroDataBox, flight status, the detection trigger.
                GET /flights/number/{number}/{date}
                Status vocabulary is theirs verbatim: Expected, EnRoute, CheckIn,
                Boarding, GateClosed, Departed, Delayed, Approaching, Arrived,
                Canceled, Diverted, CanceledUncertain, Unknown.
                https://doc.aerodatabox.com/

``flights``     Duffel, search, change and cancel air bookings. Its two-phase
                cancellation (quote first, confirm second) is exactly the shape
                compensation needs.
                POST /air/offer_requests, POST /air/order_change_requests,
                POST /air/order_changes/{id}/actions/confirm,
                POST /air/order_cancellations  -> refund_amount, refund_to, expires_at
                POST /air/order_cancellations/{id}/actions/confirm
                https://duffel.com/docs/api/order-cancellations

``lodging``     LiteAPI (Nuitée), 2M+ properties behind one REST interface, with
                a free sandbox that mirrors production.
                POST /rates, POST /rates/prebook (locks the price briefly),
                POST /rates/book, PUT /bookings/{id}/cancel
                https://docs.liteapi.travel/reference/overview

``activities``  Viator Partner API, attraction inventory, and the same
                quote-then-cancel split.
                POST /availability/check, POST /bookings/book,
                POST /bookings/cancel-quote, POST /bookings/cancel
                https://docs.viator.com/partner-api/technical/

``dining``      TableCheck, the reservation platform most Japanese restaurants
                in this price band actually run on.

``ground``      JR East / airport transfer inventory.

Live vs. fixture
----------------
``status`` will call AeroDataBox for real when ``AERODATABOX_API_KEY`` is set,
because flight status is read-only. Duffel test-mode offers and LiteAPI sandbox
rates are read when their credentials are present; incomplete or unusable result
sets fall back atomically to the fixture inventory below. Activities await
partner approval, while dining and ground are explicitly designed fixtures.
Every transaction remains simulated regardless of which read source was used.

``connector_report()`` renders this table into the UI so the wiring is visible
rather than claimed.
"""

from __future__ import annotations

import copy
import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .catalog import JST, SGT, jst, sgt
from .domain import BookingKind
from .amex_partners import match_partner

# AeroDataBox's own status vocabulary. `Canceled` (one l) is their spelling.
CANCELLING_STATUSES = {"Canceled", "CanceledUncertain"}
DISRUPTIVE_STATUSES = CANCELLING_STATUSES | {"Diverted"}

JsonTransport = Callable[[urllib.request.Request, float], Any]


def _default_transport(request: urllib.request.Request, timeout: float) -> Any:
    """Perform one bounded JSON request (injectable in every live helper)."""
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _json_request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    payload: Optional[Mapping[str, Any]] = None,
    timeout: float = 6.0,
    transport: Optional[JsonTransport] = None,
) -> Any:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    return (transport or _default_transport)(request, timeout)


@dataclass
class ConnectorSpec:
    """What this connector is a client for. Rendered in the UI verbatim."""

    key: str
    server: str
    upstream: str
    base_url: str
    docs: str
    auth_env: str
    tools: Dict[str, str]                 # tool name -> real endpoint
    adapter: str = ""
    mode: str = "fixture"
    availability: str = "available"
    read_mode: str = "fixture"
    transaction_mode: str = "fixture"

    def public(self) -> Dict[str, Any]:
        credential_present = bool(self.auth_env and os.environ.get(self.auth_env))
        configured_mode = self.read_mode if credential_present else "fixture"
        if self.availability in {"approval_required", "designed_fixture", "no_public_api"}:
            configured_mode = "fixture"
        return {
            "key": self.key,
            "adapter": self.adapter or f"rest/{self.key}",
            # Compatibility alias for one release; adapters are not MCP servers.
            "server": self.server,
            "upstream": self.upstream,
            "base_url": self.base_url,
            "docs": self.docs,
            "auth_env": self.auth_env,
            "tools": self.tools,
            "mode": configured_mode,
            "credential_present": credential_present,
            "availability": self.availability if self.availability != "available" else (
                "available" if credential_present or not self.auth_env else "credential_required"
            ),
            "capabilities": {
                "read": configured_mode,
                "transaction": self.transaction_mode,
            },
            "fallback_data": {
                "available": True,
                "type": "synthetic_dummy",
                "deterministic": True,
                "runtime_scraping": False,
            },
        }


# ---------------------------------------------------------------------------
# Detection — AeroDataBox
# ---------------------------------------------------------------------------

STATUS_SPEC = ConnectorSpec(
    key="status",
    server="mcp/flight-status",
    upstream="AeroDataBox",
    base_url="https://aerodatabox.p.rapidapi.com",
    docs="https://doc.aerodatabox.com/",
    auth_env="AERODATABOX_API_KEY",
    tools={
        "get_flight_status": "GET /flights/number/{number}/{date}",
        "get_airport_delays": "GET /airports/{codeType}/{code}/delays",
    },
    adapter="rest/aerodatabox",
    mode="live",
    read_mode="live",
)

# What the demonstration's monitor sees when it sweeps the member's flights.
# Shaped as AeroDataBox returns it, trimmed to the fields the detector reads.
FIXTURE_STATUS: Dict[str, Dict[str, Any]] = {
    "SQ638": {
        "number": "SQ 638",
        "status": "Canceled",
        "codeshareStatus": "IsOperator",
        "isCargo": False,
        "airline": {"name": "Singapore Airlines", "iata": "SQ"},
        "departure": {
            "airport": {"iata": "SIN", "name": "Singapore Changi"},
            "scheduledTime": {"local": "2026-09-18 09:00+08:00", "utc": "2026-09-18 01:00Z"},
            "terminal": "3",
        },
        "arrival": {
            "airport": {"iata": "NRT", "name": "Tokyo Narita"},
            "scheduledTime": {"local": "2026-09-18 17:05+09:00", "utc": "2026-09-18 08:05Z"},
            "terminal": "1",
        },
        "_disruption": {
            "reason": "Aircraft technical, inbound rotation withdrawn",
            "notified_at": "2026-09-18 06:02+08:00",
        },
    },
    "GK205": {
        "number": "GK 205",
        "status": "Expected",
        "airline": {"name": "Jetstar Japan", "iata": "GK"},
        "departure": {
            "airport": {"iata": "NRT", "name": "Tokyo Narita"},
            "scheduledTime": {"local": "2026-09-21 14:00+09:00", "utc": "2026-09-21 05:00Z"},
            "terminal": "3",
        },
        "arrival": {
            "airport": {"iata": "KIX", "name": "Kansai International"},
            "scheduledTime": {"local": "2026-09-21 15:40+09:00", "utc": "2026-09-21 06:40Z"},
            "terminal": "1",
        },
    },
}


def fetch_flight_status(
    flight_number: str,
    date_local: str,
    *,
    timeout: float = 6.0,
    transport: Optional[JsonTransport] = None,
) -> Dict[str, Any]:
    """One flight, one date. Live if a key is present, fixture otherwise.

    The response is normalised to the fields the detector uses, and always
    carries ``source`` so the UI can say honestly where the answer came from.
    """
    key = os.environ.get(STATUS_SPEC.auth_env)
    compact = flight_number.replace(" ", "").upper()

    if key:
        url = f"{STATUS_SPEC.base_url}/flights/number/{urllib.parse.quote(compact)}/{date_local}"
        request = urllib.request.Request(url, headers={
            "X-RapidAPI-Key": key,
            "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com",
            "Accept": "application/json",
        })
        try:
            payload = (transport or _default_transport)(request, timeout)
            legs = payload if isinstance(payload, list) else [payload]
            if legs and isinstance(legs[0], Mapping) and legs[0].get("status"):
                leg = legs[0]
                return {
                    "source": "live",
                    "synthetic": False,
                    "upstream": STATUS_SPEC.upstream,
                    "endpoint": f"GET /flights/number/{compact}/{date_local}",
                    "flight": leg,
                    "status": leg["status"],
                    "cancelled": leg.get("status") in CANCELLING_STATUSES,
                }
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as error:
            # A monitor that falls over when the upstream blips is not a monitor.
            return _fixture_status(compact, date_local, note=f"live lookup failed ({type(error).__name__}); using fixture")

    return _fixture_status(compact, date_local)


def _fixture_status(compact: str, date_local: str, note: str = "") -> Dict[str, Any]:
    leg = FIXTURE_STATUS.get(compact)
    if leg is None:
        return {
            "source": "fixture",
            "synthetic": True,
            "upstream": STATUS_SPEC.upstream,
            "endpoint": f"GET /flights/number/{compact}/{date_local}",
            "flight": None,
            "status": "Unknown",
            "cancelled": False,
            "note": note or "No fixture for this flight number.",
        }
    return {
        "source": "fixture",
        "synthetic": True,
        "upstream": STATUS_SPEC.upstream,
        "endpoint": f"GET /flights/number/{compact}/{date_local}",
        "flight": leg,
        "status": leg["status"],
        "cancelled": leg["status"] in CANCELLING_STATUSES,
        "note": note,
    }


# ---------------------------------------------------------------------------
# Recovery inventory
# ---------------------------------------------------------------------------

@dataclass
class InventoryItem:
    """One thing an agent could buy or change. Times are supplier-local."""

    id: str
    booking_id: str
    kind: BookingKind
    title: str
    detail: str
    supplier: str
    offer_id: str
    start: datetime
    end: datetime
    location: str
    place_code: str
    cost_delta: float
    changes_booking: bool
    quality: float
    notes: List[str] = field(default_factory=list)
    requires_payment: bool = True
    action: str = ""
    compensating_action: str = ""
    # True when choosing this option removes the booking from the trip entirely,
    # rather than moving it. The graph splices dropped nodes out; it does not
    # treat them as breakages, because giving something up on purpose must not
    # cascade into everything downstream of it.
    drops_booking: bool = False
    # Probability this option itself fails to deliver — a missed connection, a
    # timetable that has no fallback after it. Distinct from the graph's
    # dependency checks, which ask whether the plan works *if everything runs*.
    reliability_risk: float = 0.05
    # Amex properties relevant to this step. Rendered as outbound links, and
    # constrained to americanexpress.com over https at both ends: the allowlist
    # in the renderer is the enforcement, this list is the content.
    links: List[Dict[str, str]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    source_mode: str = "fixture"
    upstream: str = ""
    amex_partner: Optional[Dict[str, Any]] = None
    # False only for an authenticated upstream read. Fixtures are deterministic
    # synthetic dummy records and retain this label through the public option.
    synthetic: bool = True


@dataclass
class SearchResult:
    """A planning read plus honest provenance for that complete result set."""

    items: List[InventoryItem]
    mode: str
    upstream: str
    fallback_reason: str = ""
    latency_ms: Optional[int] = None


@dataclass
class SearchContext:
    """Request-owned cache and injectable network boundary."""

    transport: Optional[JsonTransport] = None
    timeout: float = 6.0
    cache: Dict[Tuple[str, str], SearchResult] = field(default_factory=dict)

    async def search(
        self,
        connector: str,
        booking_id: str,
        *,
        task: Any = None,
        itinerary: Any = None,
    ) -> SearchResult:
        key = (connector, booking_id)
        if key not in self.cache:
            self.cache[key] = await _search_uncached(
                connector,
                booking_id,
                task=task,
                itinerary=itinerary,
                transport=self.transport,
                timeout=self.timeout,
            )
        # Callers may sort/filter items; don't let that mutate the cached snapshot.
        return copy.deepcopy(self.cache[key])


FLIGHTS_SPEC = ConnectorSpec(
    key="flights",
    server="mcp/duffel-air",
    upstream="Duffel",
    base_url="https://api.duffel.com",
    docs="https://duffel.com/docs/api/order-change-offers",
    auth_env="DUFFEL_ACCESS_TOKEN",
    tools={
        "search_offers": "POST /air/offer_requests",
        "request_change": "POST /air/order_change_requests",
        "confirm_change": "POST /air/order_changes/{id}/actions/confirm",
        "quote_cancellation": "POST /air/order_cancellations",
        "confirm_cancellation": "POST /air/order_cancellations/{id}/actions/confirm",
    },
    adapter="rest/duffel",
    read_mode="sandbox",
)

LODGING_SPEC = ConnectorSpec(
    key="lodging",
    server="mcp/liteapi-lodging",
    upstream="LiteAPI (Nuitée)",
    base_url="https://api.liteapi.travel/v3.0",
    docs="https://docs.liteapi.travel/reference/overview",
    auth_env="LITEAPI_SANDBOX_KEY",
    tools={
        "search_rates": "POST /hotels/rates",
        "prebook": "POST /rates/prebook",
        "book": "POST /rates/book",
        "cancel": "PUT /bookings/{bookingId}/cancel",
    },
    adapter="rest/liteapi",
    read_mode="sandbox",
)

ACTIVITIES_SPEC = ConnectorSpec(
    key="activities",
    server="mcp/viator-activities",
    upstream="Viator Partner API",
    base_url="https://api.viator.com/partner",
    docs="https://docs.viator.com/partner-api/technical/",
    auth_env="VIATOR_API_KEY",
    tools={
        "check_availability": "POST /availability/check",
        "book": "POST /bookings/book",
        "quote_cancellation": "POST /bookings/cancel-quote",
        "cancel": "POST /bookings/cancel",
    },
    adapter="rest/viator",
    availability="approval_required",
)

DINING_SPEC = ConnectorSpec(
    key="dining",
    server="mcp/tablecheck-dining",
    upstream="TableCheck",
    base_url="https://www.tablecheck.com",
    docs="https://www.tablecheck.com/en/join/",
    auth_env="",
    tools={
        "search_availability": "fixture://dining/availability",
        "modify_reservation": "fixture://dining/reservations/{id}",
        "cancel_reservation": "fixture://dining/reservations/{id}/cancel",
    },
    adapter="fixture/dining",
    availability="designed_fixture",
)

GROUND_SPEC = ConnectorSpec(
    key="ground",
    server="mcp/ground-transfer",
    upstream="JR East / airport transfer desk",
    base_url="https://www.jreast.co.jp/multi/en/nex/",
    docs="https://www.jreast.co.jp/multi/en/nex/",
    auth_env="",
    tools={
        "search": "fixture://ground/connections",
        "reserve": "fixture://ground/reservations",
        "cancel": "fixture://ground/reservations/{id}/cancel",
    },
    adapter="fixture/ground",
    availability="no_public_api",
)

SPECS: Dict[str, ConnectorSpec] = {
    spec.key: spec
    for spec in (STATUS_SPEC, FLIGHTS_SPEC, LODGING_SPEC, ACTIVITIES_SPEC, DINING_SPEC, GROUND_SPEC)
}


# -- Replacement air inventory ----------------------------------------------
# Signed against what the member already paid: negative means money comes back.

def _flight_inventory() -> List[InventoryItem]:
    common = dict(
        booking_id="bk_flight_out",
        kind=BookingKind.FLIGHT,
        location="Singapore Changi → Narita",
        place_code="SIN-NRT",
        changes_booking=True,
        action="flights.confirm_change",
        compensating_action="flights.confirm_cancellation",
    )
    return [
        InventoryItem(
            id="opt_flt_sq12", offer_id="off_0000ASQ012Vn4",
            title="SQ12 · same-day evening direct",
            detail="Business class · direct · departs 13:45 SGT, arrives 21:50 JST",
            supplier="Singapore Airlines",
            start=sgt(18, 13, 45), end=jst(18, 21, 50),
            cost_delta=180.0, quality=0.94,
            notes=["Same aircraft type and cabin as the cancelled flight.",
                   "Arrives in time for the hotel's 23:59 desk cut-off."],
            meta={"hours_lost": 4.75, "stops": "Direct", "stops_status": "good", "cabin": "Business class"},
            **common,
            reliability_risk=0.12,
            links=[{"label": "View Terminal 3 lounge access", "url": "https://www.americanexpress.com/en-sg/travel/lounges/the-platinum-card/SIN/sats-premier-lounge-terminal3-RL47nYNPjn/"}],
        ),
        InventoryItem(
            id="opt_flt_nh802", offer_id="off_0000ANH802Qb7",
            title="NH802 · earlier same-day direct",
            detail="Business class · direct · departs 11:30 SGT, arrives 19:35 JST",
            supplier="All Nippon Airways",
            start=sgt(18, 11, 30), end=jst(18, 19, 35),
            cost_delta=320.0, quality=0.97,
            notes=["Shortest delay available on the day.",
                   "Star Alliance, status and lounge access carry over."],
            meta={"hours_lost": 2.5, "stops": "Direct", "stops_status": "good", "cabin": "Business class"},
            **common,
            reliability_risk=0.1,
            links=[{"label": "View Terminal 3 lounge access", "url": "https://www.americanexpress.com/en-sg/travel/lounges/the-platinum-card/SIN/sats-premier-lounge-terminal3-RL47nYNPjn/"}],
        ),
        InventoryItem(
            id="opt_flt_cx715", offer_id="off_0000ACX715Rt1",
            title="CX715 · next morning via Hong Kong",
            detail="Business class · 1 stop, HKG 1h 50m · departs 19 Sep 08:10, arrives 19:40 JST",
            supplier="Cathay Pacific",
            start=sgt(19, 8, 10), end=jst(19, 19, 40),
            cost_delta=-400.0, quality=0.71,
            notes=["Materially cheaper, but the connection adds a failure point.",
                   "Arrival is after the Disneyland passport date has started."],
            meta={"hours_lost": 26.6, "stops": "1 stop · HKG", "stops_status": "warn", "cabin": "Business class"},
            **common,
            reliability_risk=0.28,
            links=[{"label": "View Terminal 3 lounge access", "url": "https://www.americanexpress.com/en-sg/travel/lounges/the-platinum-card/SIN/sats-premier-lounge-terminal3-RL47nYNPjn/"}],
        ),
        InventoryItem(
            id="opt_flt_nh860", offer_id="off_0000ANH860Zz8",
            title="NH860 · in two days, direct",
            detail="Business class · direct · departs 20 Sep 06:55 SGT, arrives 15:05 JST",
            supplier="All Nippon Airways",
            start=sgt(20, 6, 55), end=jst(20, 15, 5),
            cost_delta=-1150.0, quality=0.52,
            notes=["Cheapest fare on the route, at the cost of two nights of the trip.",
                   "Writes off most of the Tokyo leg."],
            meta={"hours_lost": 46.0, "stops": "Direct", "stops_status": "good", "cabin": "Business class"},
            **common,
            reliability_risk=0.18,
            links=[{"label": "View Terminal 3 lounge access", "url": "https://www.americanexpress.com/en-sg/travel/lounges/the-platinum-card/SIN/sats-premier-lounge-terminal3-RL47nYNPjn/"}],
        ),
    ]


def _lodging_inventory() -> List[InventoryItem]:
    tokyo = dict(
        booking_id="bk_hotel_tokyo",
        kind=BookingKind.LODGING,
        location="Maihama, Urayasu",
        place_code="TYO",
        action="lodging.book",
        compensating_action="lodging.cancel",
    )
    return [
        InventoryItem(
            id="opt_lod_keep", offer_id="rate_keep_9QR41K7",
            title="Keep the booking, note a late arrival",
            detail="Hilton Tokyo Bay holds the room; the desk is told to expect a late check-in",
            supplier="Hilton Tokyo Bay",
            start=jst(18, 22, 30), end=jst(21, 11, 0),
            cost_delta=0.0, changes_booking=False, quality=0.96,
            notes=["No money moves. The property is notified, nothing is rebooked."],
            requires_payment=False,
            meta={"nights": 3},
            **tokyo,
            reliability_risk=0.04,
            links=[{"label": "Explore Amex hotels in Tokyo", "url": "https://www.americanexpress.com/en-sg/travel/discover/property-results/c/27/dt/4/d/Tokyo"}],
        ),
        InventoryItem(
            id="opt_lod_drop1", offer_id="rate_amend_9QR41K7_n2",
            title="Release the first night, keep nights 2 and 3",
            detail="Check-in moves to 19 Sep · first night is non-refundable and is forfeited",
            supplier="Hilton Tokyo Bay",
            start=jst(19, 15, 0), end=jst(21, 11, 0),
            cost_delta=300.0, changes_booking=True, quality=0.74,
            notes=["The forfeited night is eligible under trip interruption protection."],
            meta={"nights": 2, "forfeited": 300.0},
            **tokyo,
            reliability_risk=0.06,
            links=[{"label": "Explore Amex hotels in Tokyo", "url": "https://www.americanexpress.com/en-sg/travel/discover/property-results/c/27/dt/4/d/Tokyo"}, {"label": "Browse Love Dining hotel partners", "url": "https://www.americanexpress.com/sg/benefits/love-dining/love-dining-hotels.html"}],
        ),
        InventoryItem(
            id="opt_lod_drop2", offer_id="rate_amend_9QR41K7_n1",
            title="Release nights 1 and 2, keep the last night",
            detail="Check-in moves to 20 Sep · two nights forfeited",
            supplier="Hilton Tokyo Bay",
            start=jst(20, 15, 0), end=jst(21, 11, 0),
            cost_delta=600.0, changes_booking=True, quality=0.48,
            notes=["Most of the Tokyo stay is written off."],
            meta={"nights": 1, "forfeited": 600.0},
            **tokyo,
            reliability_risk=0.06,
            links=[{"label": "Explore Amex hotels in Tokyo", "url": "https://www.americanexpress.com/en-sg/travel/discover/property-results/c/27/dt/4/d/Tokyo"}, {"label": "Browse Love Dining hotel partners", "url": "https://www.americanexpress.com/sg/benefits/love-dining/love-dining-hotels.html"}],
        ),
        InventoryItem(
            id="opt_lod_transit", offer_id="rate_new_CHG_TRANSIT",
            title="Add a Changi transit hotel for tonight",
            detail="Aerotel Singapore, airside · overnight block while waiting for the morning departure",
            supplier="Aerotel Singapore",
            start=sgt(18, 22, 0), end=sgt(19, 6, 30),
            cost_delta=90.0, changes_booking=True, quality=0.80,
            notes=["Airside, so there is no immigration round-trip before the 08:10 departure.",
                   "Charged to the same Card, no new payment method to set up."],
            meta={"supplementary": True},
            booking_id="bk_hotel_tokyo_supplement",
            kind=BookingKind.LODGING,
            location="Changi Airport T1, airside",
            place_code="SIN",
            action="lodging.book",
            compensating_action="lodging.cancel",
            reliability_risk=0.08,
            links=[{"label": "Search Amex Travel hotels", "url": "https://www.americanexpress.com/en-sg/travel/hotels/"}],
        ),
    ]


def _activity_inventory() -> List[InventoryItem]:
    common = dict(
        booking_id="bk_activity_tdl",
        kind=BookingKind.ACTIVITY,
        location="Maihama, Urayasu",
        place_code="TYO",
        action="activities.book",
        compensating_action="activities.cancel",
    )
    return [
        InventoryItem(
            id="opt_act_keep", offer_id="prod_TDL_1DAY_19",
            title="Keep the 19 September passport",
            detail="Dated entry unchanged, park opens 09:00",
            supplier="Tokyo Disney Resort",
            start=jst(19, 9, 0), end=jst(19, 20, 0),
            cost_delta=0.0, changes_booking=False, quality=1.0,
            notes=["Only viable if the member is in Tokyo the night before."],
            requires_payment=False,
            **common,
            reliability_risk=0.03,
            links=[{"label": "Explore Amex hotels in Tokyo", "url": "https://www.americanexpress.com/en-sg/travel/discover/property-results/c/27/dt/4/d/Tokyo"}],
        ),
        InventoryItem(
            id="opt_act_move20", offer_id="prod_TDL_1DAY_20",
            title="Move the passport to 20 September",
            detail="Same 1-Day Passport, re-dated · full day, park opens 09:00",
            supplier="Tokyo Disney Resort",
            start=jst(20, 9, 0), end=jst(20, 20, 0),
            cost_delta=12.0, changes_booking=True, quality=0.93,
            notes=["Date change on a web passport carries a small re-issue fee."],
            **common,
            reliability_risk=0.05,
            links=[{"label": "Explore Amex hotels in Tokyo", "url": "https://www.americanexpress.com/en-sg/travel/discover/property-results/c/27/dt/4/d/Tokyo"}],
        ),
        InventoryItem(
            id="opt_act_move20pm", offer_id="prod_TDL_AFTER6_20",
            title="Downgrade to a 20 September evening passport",
            detail="After-6 passport · entry from 18:00",
            supplier="Tokyo Disney Resort",
            start=jst(20, 18, 0), end=jst(20, 22, 0),
            cost_delta=-34.0, changes_booking=True, quality=0.55,
            notes=["Cheaper than the full day, but four hours in the park instead of eleven."],
            **common,
            reliability_risk=0.05,
            links=[{"label": "Explore Amex hotels in Tokyo", "url": "https://www.americanexpress.com/en-sg/travel/discover/property-results/c/27/dt/4/d/Tokyo"}],
        ),
        InventoryItem(
            id="opt_act_refund", offer_id="cancel_BR-556320117",
            title="Cancel the passport and take the refund",
            detail="Fully refundable up to the dated entry",
            supplier="Tokyo Disney Resort",
            start=jst(19, 9, 0), end=jst(19, 9, 0),
            cost_delta=-80.0, changes_booking=True, quality=0.30,
            notes=["The money comes back. The day does not."],
            requires_payment=False,
            drops_booking=True,
            **common,
            reliability_risk=0.0,
            links=[{"label": "Explore Amex hotels in Tokyo", "url": "https://www.americanexpress.com/en-sg/travel/discover/property-results/c/27/dt/4/d/Tokyo"}],
        ),
    ]


def _dining_inventory() -> List[InventoryItem]:
    common = dict(
        booking_id="bk_dining_tokyo",
        kind=BookingKind.DINING,
        location="Shinjuku, Tokyo",
        place_code="TYO",
        action="dining.modify_reservation",
        compensating_action="dining.cancel_reservation",
    )
    return [
        InventoryItem(
            id="opt_din_keep", offer_id="tc_res_88213",
            title="Keep the 20:00 seating",
            detail="Two seats at the counter, 18 Sep",
            supplier="TableCheck",
            start=jst(18, 20, 0), end=jst(18, 21, 30),
            cost_delta=0.0, changes_booking=False, quality=1.0,
            requires_payment=False,
            **common,
            reliability_risk=0.03,
            links=[{"label": "Browse Love Dining restaurants", "url": "https://www.americanexpress.com/sg/benefits/love-dining/love-restaurants.html"}],
        ),
        InventoryItem(
            id="opt_din_late", offer_id="tc_slot_18_2130",
            title="Move to the 21:30 seating tonight",
            detail="Last counter seating, 18 Sep · deposit carries over",
            supplier="TableCheck",
            start=jst(18, 21, 30), end=jst(18, 23, 0),
            cost_delta=0.0, changes_booking=True, quality=0.88,
            notes=["The restaurant holds the deposit against the new slot."],
            requires_payment=False,
            **common,
            reliability_risk=0.09,
            links=[{"label": "Browse Love Dining restaurants", "url": "https://www.americanexpress.com/sg/benefits/love-dining/love-restaurants.html"}],
        ),
        InventoryItem(
            id="opt_din_move19", offer_id="tc_slot_19_2000",
            title="Move to 19 September, 20:00",
            detail="Same counter, next evening · deposit carries over",
            supplier="TableCheck",
            start=jst(19, 20, 0), end=jst(19, 21, 30),
            cost_delta=0.0, changes_booking=True, quality=0.9,
            requires_payment=False,
            **common,
            reliability_risk=0.06,
            links=[{"label": "Browse Love Dining restaurants", "url": "https://www.americanexpress.com/sg/benefits/love-dining/love-restaurants.html"}],
        ),
        InventoryItem(
            id="opt_din_cancel", offer_id="tc_cancel_88213",
            title="Cancel, deposit is forfeited",
            detail="Same-day cancellation, no refund of the deposit",
            supplier="TableCheck",
            start=jst(18, 20, 0), end=jst(18, 20, 0),
            cost_delta=180.0, changes_booking=True, quality=0.2,
            notes=["A same-day cancellation forfeits the full deposit."],
            requires_payment=False,
            drops_booking=True,
            **common,
            reliability_risk=0.0,
            links=[{"label": "Browse Love Dining restaurants", "url": "https://www.americanexpress.com/sg/benefits/love-dining/love-restaurants.html"}],
        ),
    ]


def _ground_inventory() -> List[InventoryItem]:
    common = dict(
        booking_id="bk_transfer_nex",
        kind=BookingKind.GROUND,
        location="Narita Airport → Tokyo",
        place_code="NRT-TYO",
        action="ground.reserve",
        compensating_action="ground.cancel",
    )
    return [
        InventoryItem(
            id="opt_gnd_keep", offer_id="jre_NEX_4471882",
            title="Keep the 18:15 Narita Express",
            detail="Reserved Green Car seat",
            supplier="JR East",
            start=jst(18, 18, 15), end=jst(18, 19, 20),
            cost_delta=0.0, changes_booking=False, quality=1.0,
            requires_payment=False,
            **common,
            reliability_risk=0.05,
            links=[{"label": "View Amex Travel car hire", "url": "https://www.americanexpress.com/en-sg/travel/cars/"}],
        ),
        InventoryItem(
            id="opt_gnd_nex_late", offer_id="jre_NEX_late",
            title="Move to the 23:00 Narita Express",
            detail="Last N'EX of the night · seat reservation re-issued at no fare difference",
            supplier="JR East",
            start=jst(18, 23, 0), end=jst(19, 0, 5),
            cost_delta=8.0, changes_booking=True, quality=0.82,
            notes=["Last departure, a further slip means a road transfer."],
            **common,
            reliability_risk=0.16,
            links=[{"label": "View Amex Travel car hire", "url": "https://www.americanexpress.com/en-sg/travel/cars/"}],
        ),
        InventoryItem(
            id="opt_gnd_private", offer_id="tfr_private_NRT",
            title="Private car from Narita",
            detail="Meet-and-greet at arrivals, direct to the property, any hour",
            supplier="Airport transfer desk",
            start=jst(18, 22, 40), end=jst(18, 23, 55),
            cost_delta=75.0, changes_booking=True, quality=0.95,
            notes=["Immune to the rail timetable, the reason it is worth 75 on a late arrival."],
            **common,
            reliability_risk=0.04,
            links=[{"label": "View Amex Travel car hire", "url": "https://www.americanexpress.com/en-sg/travel/cars/"}],
        ),
        InventoryItem(
            id="opt_gnd_next_day", offer_id="jre_NEX_next",
            title="Re-issue for the next arrival day",
            detail="Seat reservation moved to match the new flight",
            supplier="JR East",
            start=jst(19, 20, 45), end=jst(19, 21, 50),
            cost_delta=0.0, changes_booking=True, quality=0.85,
            **common,
            reliability_risk=0.07,
            links=[{"label": "View Amex Travel car hire", "url": "https://www.americanexpress.com/en-sg/travel/cars/"}],
        ),
        InventoryItem(
            id="opt_gnd_refund", offer_id="jre_refund_4471882",
            title="Cancel the transfer and refund it",
            detail="Unused seat reservations are refundable before departure",
            supplier="JR East",
            start=jst(18, 18, 15), end=jst(18, 18, 15),
            cost_delta=-45.0, changes_booking=True, quality=0.4,
            notes=["Refunded net of a JR handling charge.",
                   "Leaves the member on the unreserved Keisei local service, paid at the gate."],
            requires_payment=False,
            drops_booking=True,
            **common,
            reliability_risk=0.14,
            links=[{"label": "View Amex Travel car hire", "url": "https://www.americanexpress.com/en-sg/travel/cars/"}],
        ),
    ]


def _domestic_inventory() -> List[InventoryItem]:
    common = dict(
        booking_id="bk_flight_dom",
        kind=BookingKind.FLIGHT,
        location="Narita → Kansai",
        place_code="NRT-KIX",
        action="flights.confirm_change",
        compensating_action="flights.confirm_cancellation",
    )
    return [
        InventoryItem(
            id="opt_dom_keep", offer_id="ord_0000AGK205Pm2",
            title="Keep GK205 on 21 September",
            detail="Departs 14:00, arrives 15:40",
            supplier="Jetstar Japan",
            start=jst(21, 14, 0), end=jst(21, 15, 40),
            cost_delta=0.0, changes_booking=False, quality=1.0,
            requires_payment=False,
            **common,
            reliability_risk=0.06,
            links=[{"label": "View Amex Travel car hire", "url": "https://www.americanexpress.com/en-sg/travel/cars/"}],
        ),
        InventoryItem(
            id="opt_dom_late", offer_id="off_0000AGK211Ww3",
            title="Move to GK211, 21 September 19:15",
            detail="Later departure the same day · change fee applies",
            supplier="Jetstar Japan",
            start=jst(21, 19, 15), end=jst(21, 20, 55),
            cost_delta=55.0, changes_booking=True, quality=0.86,
            notes=["Low-cost fare: changeable for a fee, never refundable."],
            **common,
            reliability_risk=0.11,
            links=[{"label": "View Amex Travel car hire", "url": "https://www.americanexpress.com/en-sg/travel/cars/"}],
        ),
    ]


# Amex's own programme listing pages, per city and per collection. A supplier's
# marketing page is deliberately not used as a link here: see amex_partners.py.
_AMEX_OSAKA = ("https://www.americanexpress.com/en-sg/travel/discover/"
               "property-results/c/27/dt/4/d/Osaka%2CJapan")
_AMEX_FHR = "https://www.americanexpress.com/en-sg/travel/discover/fine-hotels-resorts/"
_AMEX_HOTELS = "https://www.americanexpress.com/en-sg/travel/hotels/"


def _lodging_osaka_inventory() -> List[InventoryItem]:
    """Recovery options for the Osaka leg.

    The domestic flight lands after the original 16:00 check-in, so this task is
    always reachable once the outbound is disrupted. Without these the
    Accommodation Agent searched, found nothing and failed the task outright.
    """
    osaka = dict(
        booking_id="bk_hotel_osaka",
        kind=BookingKind.LODGING,
        location="Umeda, Osaka",
        place_code="OSA",
        action="lodging.book",
        compensating_action="lodging.cancel",
    )
    return [
        InventoryItem(
            id="opt_lod_osa_keep", offer_id="rate_keep_2XT80M4",
            title="Keep the booking, flag a late arrival",
            detail="Hotel Granvia Osaka holds the room and the desk is told to expect a late check-in",
            supplier="Hotel Granvia Osaka",
            start=jst(21, 22, 0), end=jst(23, 11, 0),
            cost_delta=0.0, changes_booking=False, quality=0.95,
            notes=["No money moves and the rate stays fully refundable."],
            requires_payment=False,
            meta={"nights": 2},
            **osaka,
            reliability_risk=0.04,
            links=[{"label": "Hotel Granvia Osaka on Amex Travel", "url": _AMEX_OSAKA}],
        ),
        InventoryItem(
            id="opt_lod_osa_shift", offer_id="rate_amend_2XT80M4_late",
            title="Shift check-in to 22 September",
            detail="One night released at no charge because the rate cancels free up to 24 hours ahead",
            supplier="Hotel Granvia Osaka",
            start=jst(22, 16, 0), end=jst(23, 11, 0),
            cost_delta=-280.0, changes_booking=True, quality=0.71,
            notes=["The released night is refunded in full, not forfeited."],
            meta={"nights": 1, "refunded": 280.0},
            **osaka,
            reliability_risk=0.05,
            links=[{"label": "Manage this rate on Amex Travel", "url": _AMEX_OSAKA}],
        ),
        InventoryItem(
            id="opt_lod_osa_station", offer_id="rate_new_OSA_STATION",
            title="Move to a hotel inside Osaka Station",
            detail="Same concourse as the arrival platform, so a late landing still makes check-in",
            supplier="Amex Travel, Osaka Station properties",
            start=jst(21, 21, 0), end=jst(23, 11, 0),
            cost_delta=64.0, changes_booking=True, quality=0.83,
            notes=["Charged to the same Card, so no new payment method is needed."],
            meta={"nights": 2},
            **osaka,
            reliability_risk=0.07,
            links=[{"label": "Compare Osaka Station hotels", "url": _AMEX_HOTELS}],
        ),
        InventoryItem(
            id="opt_lod_osa_fhr", offer_id="rate_new_OSA_FHR",
            title="Rebook into a Fine Hotels + Resorts property",
            detail="Adds credit, late checkout and breakfast on the Platinum Card benefit",
            supplier="Amex Fine Hotels + Resorts",
            start=jst(21, 21, 30), end=jst(23, 12, 0),
            cost_delta=190.0, changes_booking=True, quality=0.90,
            notes=["Benefits apply only when the stay is booked through Amex Travel."],
            meta={"nights": 2},
            **osaka,
            reliability_risk=0.06,
            links=[{"label": "Fine Hotels + Resorts", "url": _AMEX_FHR}],
        ),
    ]


# What each connector tool *does*, in the language the trace should speak. The
# raw endpoint is kept alongside it rather than thrown away, so the technical
# reading is still one hover away.
TOOL_LABELS: Dict[str, str] = {
    "search_offers": "Search replacement fares",
    "request_change": "Request a change quote",
    "confirm_change": "Confirm the change",
    "quote_cancellation": "Quote the cancellation",
    "confirm_cancellation": "Confirm the cancellation",
    "search_rates": "Search room rates",
    "prebook": "Hold the rate",
    "book": "Book the room",
    "cancel": "Cancel the booking",
    "search_products": "Search tickets",
    "availability": "Check availability",
    "search_availability": "Check availability",
    "modify_reservation": "Move the reservation",
    "cancel_reservation": "Cancel the reservation",
    "search_schedules": "Search departures",
}


def tool_label(tool: str) -> str:
    """Plain-English name for a connector tool, falling back to its key."""
    return TOOL_LABELS.get(tool, tool.replace("_", " ").strip().capitalize())


# ---------------------------------------------------------------------------
# Per-option links
# ---------------------------------------------------------------------------
#
# A recommendation the member cannot act on is only half an answer. The links
# used to be written by hand per fixture and said things like "Explore Amex
# hotels in Tokyo" against every option in the plan, which is the same sentence
# whichever booking the AI picked.
#
# These are built from the option itself, so the label names the supplier and
# the date the member is actually being sent to book, and live inventory gets
# the same treatment as a fixture without anyone editing a list.
#
# The destinations are Amex's own programme and search pages, the ones already
# reviewed in amex_partners.py. No property-specific URL is invented here: that
# module requires a human to verify a record before a supplier gets a link of
# its own, and a plausible-looking guess is worse than an honest search page.

_AMEX = "https://www.americanexpress.com"
_CITY_PROPERTIES = {
    "TYO": f"{_AMEX}/en-sg/travel/discover/property-results/c/27/dt/4/d/Tokyo%2CJapan",
    "OSA": f"{_AMEX}/en-sg/travel/discover/property-results/c/27/dt/4/d/Osaka%2CJapan",
    "SIN": f"{_AMEX}/en-sg/travel/discover/property-results/c/27/dt/4/d/Singapore",
}
_AMEX_HOTELS = f"{_AMEX}/en-sg/travel/hotels/"
_AMEX_FLIGHTS = f"{_AMEX}/en-sg/travel/flights/"
_AMEX_CARS = f"{_AMEX}/en-sg/travel/cars/"
_LOVE_DINING = f"{_AMEX}/sg/benefits/love-dining/love-dining-restaurants.html"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _when(value: Any) -> str:
    """`18 Sep`, so the label says which date it is sending the member to."""
    try:
        return f"{value.day} {_MONTHS[value.month - 1]}"
    except Exception:
        return ""


def option_links(item: Any) -> List[Dict[str, str]]:
    """One specific, actionable link for this option."""
    kind = getattr(item, "kind", None)
    supplier = str(getattr(item, "supplier", "") or "").strip()
    place = str(getattr(item, "place_code", "") or "").strip().upper()[:3]
    when = _when(getattr(item, "start", None))

    # An option that drops a booking is a refund, not something to go and book.
    if getattr(item, "drops_booking", False):
        return []

    if kind is BookingKind.LODGING:
        url = _CITY_PROPERTIES.get(place, _AMEX_HOTELS)
        label = f"Book {supplier}" if supplier else "Find a hotel"
        if when:
            label = f"{label}, {when}"
    elif kind is BookingKind.FLIGHT:
        url = _AMEX_FLIGHTS
        number = str((getattr(item, "meta", None) or {}).get("flight_number", "")).strip()
        subject = number or supplier or "this flight"
        label = f"Book {subject}" + (f", {when}" if when else "")
    elif kind is BookingKind.DINING:
        url = _LOVE_DINING
        label = f"Book {supplier}" if supplier else "Find a restaurant"
        if when:
            label = f"{label}, {when}"
    elif kind is BookingKind.GROUND:
        url = _AMEX_CARS
        label = f"Arrange {supplier}" if supplier else "Arrange transfer"
    elif kind is BookingKind.ACTIVITY:
        url = _AMEX_HOTELS
        label = f"Book {supplier}" if supplier else "Book this experience"
        if when:
            label = f"{label}, {when}"
    else:
        return []

    return [{"label": label[:60], "url": url}]


INVENTORY: List[InventoryItem] = (
    _flight_inventory()
    + _lodging_inventory()
    + _lodging_osaka_inventory()
    + _activity_inventory()
    + _dining_inventory()
    + _ground_inventory()
    + _domestic_inventory()
)

INVENTORY_BY_ID: Dict[str, InventoryItem] = {item.id: item for item in INVENTORY}


def search(connector: str, booking_id: str) -> List[InventoryItem]:
    """Compatibility fixture read used by the original synchronous agents."""
    return [
        item for item in INVENTORY
        if item.booking_id in (booking_id, f"{booking_id}_supplement")
        and SPECS[connector].key == connector
        and _owns(connector, item)
    ]


def inventory_item_snapshot(item: InventoryItem) -> Dict[str, Any]:
    """JSON-safe request/session snapshot for later simulated execution."""
    value = asdict(item)
    value["kind"] = item.kind.value
    value["start"] = item.start.isoformat()
    value["end"] = item.end.isoformat()
    return value


def inventory_item_from_snapshot(value: Mapping[str, Any]) -> InventoryItem:
    """Restore a validated-enough internal item from our own stored snapshot."""
    payload = dict(value)
    payload["kind"] = BookingKind(payload["kind"])
    payload["start"] = _iso_datetime(payload["start"])
    payload["end"] = _iso_datetime(payload["end"])
    return InventoryItem(**payload)


_OWNERSHIP = {
    BookingKind.FLIGHT: "flights",
    BookingKind.LODGING: "lodging",
    BookingKind.ACTIVITY: "activities",
    BookingKind.DINING: "dining",
    BookingKind.GROUND: "ground",
}


def _market_for(item: InventoryItem) -> str:
    return "SG" if item.place_code == "SIN" or item.place_code.startswith("SIN-") else "JP"


for _item in INVENTORY:
    _connector_key = _OWNERSHIP[_item.kind]
    _item.source_mode = "fixture"
    _item.upstream = SPECS[_connector_key].upstream
    _item.meta.setdefault("source_mode", "fixture")
    _item.meta.setdefault("upstream", SPECS[_connector_key].upstream)
    _partner = match_partner(_item.supplier, category=_item.kind.value, market=_market_for(_item))
    if _partner:
        _item.amex_partner = _partner
        _item.meta["amex_partner"] = _partner


def _owns(connector: str, item: InventoryItem) -> bool:
    return _OWNERSHIP[item.kind] == connector


def _fixture_search_result(connector: str, booking_id: str, reason: str = "") -> SearchResult:
    return SearchResult(
        items=copy.deepcopy(search(connector, booking_id)),
        mode="fixture",
        upstream=SPECS[connector].upstream,
        fallback_reason=reason,
    )


def _iso_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("missing timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _booking_parameters(booking_id: str, itinerary: Any = None) -> Dict[str, Any]:
    booking = None
    bookings = getattr(itinerary, "bookings", None)
    if isinstance(bookings, Mapping):
        booking = bookings.get(booking_id)
    defaults: Dict[str, Dict[str, Any]] = {
        "bk_flight_out": {
            "origin": "SIN", "destination": "NRT", "departure_date": "2026-09-18",
            "cabin": "business", "refundable": 2800.0, "original_arrival": jst(18, 17, 5),
        },
        "bk_flight_dom": {
            "origin": "NRT", "destination": "KIX", "departure_date": "2026-09-21",
            "cabin": "economy", "refundable": 0.0, "original_arrival": jst(21, 15, 40),
        },
        "bk_hotel_tokyo": {
            "checkin": "2026-09-18", "checkout": "2026-09-21", "city": "Tokyo",
            "place_code": "TYO", "refundable": 600.0,
        },
        "bk_hotel_osaka": {
            "checkin": "2026-09-21", "checkout": "2026-09-23", "city": "Osaka",
            "place_code": "OSA", "refundable": 560.0,
        },
    }
    params = dict(defaults.get(booking_id, {}))
    if booking is not None:
        params["refundable"] = float(getattr(booking, "refundable", params.get("refundable", 0.0)))
        start, end = getattr(booking, "start", None), getattr(booking, "end", None)
        if getattr(booking, "kind", None) is BookingKind.LODGING and start and end:
            params.update(checkin=start.date().isoformat(), checkout=end.date().isoformat())
        if getattr(booking, "kind", None) is BookingKind.FLIGHT and start and end:
            params.update(departure_date=start.date().isoformat(), original_arrival=end)
    return params


def _duffel_search(
    booking_id: str,
    *,
    itinerary: Any = None,
    transport: Optional[JsonTransport] = None,
    timeout: float = 6.0,
) -> SearchResult:
    token = os.environ.get(FLIGHTS_SPEC.auth_env)
    if not token:
        return _fixture_search_result("flights", booking_id, "missing_credential")
    params = _booking_parameters(booking_id, itinerary)
    required = {"origin", "destination", "departure_date", "original_arrival"}
    if not required <= params.keys():
        return _fixture_search_result("flights", booking_id, "unsupported_booking")
    payload = {"data": {
        "slices": [{"origin": params["origin"], "destination": params["destination"],
                    "departure_date": params["departure_date"]}],
        "passengers": [{"type": "adult"}], "cabin_class": params.get("cabin", "economy"),
    }}
    try:
        raw = _json_request(
            "POST", f"{FLIGHTS_SPEC.base_url}/air/offer_requests?return_offers=true",
            headers={"Authorization": f"Bearer {token}", "Duffel-Version": "v2",
                     "Accept": "application/json", "Content-Type": "application/json"},
            payload=payload, timeout=timeout, transport=transport,
        )
        data = raw.get("data", {}) if isinstance(raw, Mapping) else {}
        offers = data.get("offers", []) if isinstance(data, Mapping) else []
        if isinstance(offers, Mapping):
            offers = offers.get("data", [])
        items = _normalize_duffel_offers(offers, booking_id, params)
        if len(items) < 2:
            return _fixture_search_result("flights", booking_id, "insufficient_eligible_offers")
        return SearchResult(items=items[:4], mode="sandbox", upstream=FLIGHTS_SPEC.upstream)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as error:
        return _fixture_search_result("flights", booking_id, f"upstream_{type(error).__name__.lower()}")


def _normalize_duffel_offers(
    offers: Any, booking_id: str, params: Mapping[str, Any],
) -> List[InventoryItem]:
    normalized: List[InventoryItem] = []
    if not isinstance(offers, Sequence) or isinstance(offers, (str, bytes)):
        return normalized
    for offer in offers:
        try:
            if not isinstance(offer, Mapping) or offer.get("total_currency") != "SGD":
                continue
            total = float(offer["total_amount"])
            expires_at = offer.get("expires_at")
            if expires_at:
                expiry = _iso_datetime(expires_at)
                if expiry <= datetime.now(expiry.tzinfo):
                    continue
            slices = offer.get("slices", [])
            segments = [segment for slice_ in slices for segment in slice_.get("segments", [])]
            if not segments:
                continue
            start = _iso_datetime(segments[0]["departing_at"])
            end = _iso_datetime(segments[-1]["arriving_at"])
            hours_lost = max(0.0, (end - params["original_arrival"]).total_seconds() / 3600)
            stops = max(0, len(segments) - len(slices))
            carrier = (offer.get("owner", {}).get("name")
                       or segments[0].get("marketing_carrier", {}).get("name") or "Duffel airline")
            numbers = [
                f"{segment.get('marketing_carrier', {}).get('iata_code', '')}{segment.get('marketing_carrier_flight_number', '')}"
                for segment in segments
            ]
            title_number = "/".join(number for number in numbers if number) or "Duffel offer"
            quality = round(max(0.35, min(0.98, 0.98 - hours_lost / 72 - stops * 0.10)), 3)
            risk = round(min(0.9, 0.08 + stops * 0.12 + (0.04 if hours_lost >= 18 else 0.0)), 3)
            normalized.append(InventoryItem(
                id=f"duffel_{offer['id']}", booking_id=booking_id, kind=BookingKind.FLIGHT,
                title=f"{title_number} · Duffel test offer",
                detail=f"{str(offer.get('cabin_class', params.get('cabin', 'economy'))).title()} · "
                       f"{'Direct' if not stops else f'{stops} stop'}",
                supplier=str(carrier), offer_id=str(offer["id"]), start=start, end=end,
                location=f"{params['origin']} → {params['destination']}",
                place_code=f"{params['origin']}-{params['destination']}",
                cost_delta=round(total - float(params.get("refundable", 0.0)), 2),
                changes_booking=True, quality=quality, reliability_risk=risk,
                action="flights.confirm_change", compensating_action="flights.confirm_cancellation",
                notes=["Read from Duffel test mode; any later transaction remains simulated."],
                source_mode="sandbox", upstream=FLIGHTS_SPEC.upstream,
                synthetic=False,
                meta={"hours_lost": round(hours_lost, 2),
                      "stops": "Direct" if not stops else f"{stops} stop",
                      "stops_status": "good" if not stops else "warn", "source_mode": "sandbox",
                      "upstream": FLIGHTS_SPEC.upstream, "currency": "SGD"},
            ))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return sorted(normalized, key=lambda item: (item.end, item.cost_delta, item.id))


def _liteapi_search(
    booking_id: str,
    *,
    itinerary: Any = None,
    transport: Optional[JsonTransport] = None,
    timeout: float = 6.0,
) -> SearchResult:
    key = os.environ.get(LODGING_SPEC.auth_env)
    if not key:
        return _fixture_search_result("lodging", booking_id, "missing_credential")
    params = _booking_parameters(booking_id, itinerary)
    if not {"checkin", "checkout", "city"} <= params.keys():
        return _fixture_search_result("lodging", booking_id, "unsupported_booking")
    payload: Dict[str, Any] = {
        "checkin": params["checkin"], "checkout": params["checkout"], "currency": "SGD",
        "guestNationality": "SG", "occupancies": [{"adults": 1, "children": []}],
        "cityName": params["city"],
    }
    hotel_ids = [part.strip() for part in os.environ.get("LITEAPI_HOTEL_IDS", "").split(",") if part.strip()]
    if hotel_ids:
        payload["hotelIds"] = hotel_ids
    try:
        raw = _json_request(
            "POST", f"{LODGING_SPEC.base_url}/hotels/rates",
            headers={"X-API-Key": key, "Accept": "application/json", "Content-Type": "application/json"},
            payload=payload, timeout=timeout, transport=transport,
        )
        items = _normalize_liteapi_rates(raw, booking_id, params)
        if not items:
            return _fixture_search_result("lodging", booking_id, "no_eligible_sgd_rates")
        return SearchResult(items=items[:4], mode="sandbox", upstream=LODGING_SPEC.upstream)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as error:
        return _fixture_search_result("lodging", booking_id, f"upstream_{type(error).__name__.lower()}")


def _money_value(rate: Mapping[str, Any]) -> Tuple[Optional[float], Optional[str]]:
    for key in ("suggestedSellingPrice", "retailRate", "price"):
        value = rate.get(key)
        if isinstance(value, Mapping):
            candidate = value.get("amount", value.get("total"))
            currency = value.get("currency", rate.get("currency"))
            if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes)):
                candidate = sum(float(part.get("amount", 0)) for part in candidate if isinstance(part, Mapping))
            if candidate is not None:
                return float(candidate), str(currency or "")
    candidate = rate.get("total", rate.get("amount", rate.get("total_amount")))
    if candidate is None:
        return None, None
    return float(candidate), str(rate.get("currency", rate.get("total_currency", "")))


def _normalize_liteapi_rates(raw: Any, booking_id: str, params: Mapping[str, Any]) -> List[InventoryItem]:
    data = raw.get("data", raw) if isinstance(raw, Mapping) else raw
    hotels = data.get("hotels", data.get("results", [])) if isinstance(data, Mapping) else data
    if not isinstance(hotels, Sequence) or isinstance(hotels, (str, bytes)):
        return []
    items: List[InventoryItem] = []
    start = _iso_datetime(f"{params['checkin']}T15:00:00+09:00")
    end = _iso_datetime(f"{params['checkout']}T11:00:00+09:00")
    for hotel in hotels:
        if not isinstance(hotel, Mapping):
            continue
        hotel_name = str(hotel.get("name") or hotel.get("hotelName") or "LiteAPI hotel")
        room_types = hotel.get("roomTypes", hotel.get("rooms", []))
        direct_rates = hotel.get("rates", [])
        rate_pairs = [(hotel, rate) for rate in direct_rates if isinstance(rate, Mapping)]
        for room in room_types if isinstance(room_types, Sequence) else []:
            if isinstance(room, Mapping):
                rate_pairs.extend((room, rate) for rate in room.get("rates", []) if isinstance(rate, Mapping))
        for room, rate in rate_pairs:
            try:
                total, currency = _money_value(rate)
                if total is None or currency != "SGD":
                    continue
                rate_id = str(rate.get("rateId") or rate.get("id"))
                if not rate_id or rate_id == "None":
                    continue
                rating = float(hotel.get("starRating", hotel.get("rating", 4.0)) or 4.0)
                quality = round(max(0.2, min(1.0, rating / 5.0)), 3)
                partner = match_partner(hotel_name, category="lodging", market="JP")
                meta: Dict[str, Any] = {"source_mode": "sandbox", "upstream": LODGING_SPEC.upstream,
                                        "currency": "SGD", "rating": rating}
                if partner:
                    meta["amex_partner"] = partner
                items.append(InventoryItem(
                    id=f"liteapi_{rate_id}", booking_id=booking_id, kind=BookingKind.LODGING,
                    title=f"{hotel_name} · sandbox rate",
                    detail=str(room.get("name") or room.get("roomType") or "One adult room"),
                    supplier=hotel_name, offer_id=rate_id, start=start, end=end,
                    location=str(hotel.get("address") or params["city"]),
                    place_code=str(params.get("place_code", "TYO")),
                    cost_delta=round(total - float(params.get("refundable", 0.0)), 2),
                    changes_booking=True, quality=quality,
                    reliability_risk=round(0.14 - quality * 0.08, 3),
                    action="lodging.book", compensating_action="lodging.cancel",
                    notes=["Read from LiteAPI sandbox; any later transaction remains simulated."],
                    source_mode="sandbox", upstream=LODGING_SPEC.upstream, amex_partner=partner,
                    synthetic=False,
                    meta=meta,
                    links=([{"label": f"View {partner['program']}", "url": partner["official_url"]}]
                           if partner else []),
                ))
            except (KeyError, TypeError, ValueError):
                continue
    return sorted(items, key=lambda item: (-item.quality, item.cost_delta, item.id))


async def _search_uncached(
    connector: str,
    booking_id: str,
    *,
    task: Any = None,
    itinerary: Any = None,
    transport: Optional[JsonTransport] = None,
    timeout: float = 6.0,
) -> SearchResult:
    if connector not in SPECS or connector == "status":
        raise ValueError(f"Unsupported inventory connector: {connector}")
    started = time.monotonic()
    if connector == "flights":
        result = await asyncio.to_thread(
            _duffel_search, booking_id, itinerary=itinerary, transport=transport, timeout=timeout
        )
    elif connector == "lodging":
        result = await asyncio.to_thread(
            _liteapi_search, booking_id, itinerary=itinerary, transport=transport, timeout=timeout
        )
    else:
        result = _fixture_search_result(connector, booking_id)
    result.latency_ms = round((time.monotonic() - started) * 1000)
    return result


async def search_live_or_fixture(
    connector: str,
    booking_id: str,
    *,
    task: Any = None,
    itinerary: Any = None,
    cache: Optional[Dict[Tuple[str, str], SearchResult]] = None,
    transport: Optional[JsonTransport] = None,
    timeout: float = 6.0,
) -> SearchResult:
    """Read sandbox inventory when configured, otherwise return complete fixtures.

    ``cache`` should be a fresh dictionary owned by one planning request.
    """
    context = SearchContext(transport=transport, timeout=timeout, cache=cache if cache is not None else {})
    return await context.search(connector, booking_id, task=task, itinerary=itinerary)


async def connector_health(
    key: Optional[str] = None,
    *,
    transport: Optional[JsonTransport] = None,
    timeout: float = 3.0,
) -> Dict[str, Any]:
    """Run bounded read-only checks without exposing credentials or response data."""
    requested = [key] if key else ["status", "flights", "lodging"]
    unknown = [name for name in requested if name not in SPECS]
    if unknown:
        raise ValueError(f"Unknown connector: {unknown[0]}")
    checks: List[Dict[str, Any]] = []
    for name in requested:
        started = time.monotonic()
        if name == "status":
            result = await asyncio.to_thread(
                fetch_flight_status, "SQ638", "2026-09-18", timeout=timeout, transport=transport
            )
            mode = str(result.get("source", "fixture"))
            count = int(result.get("flight") is not None)
            fallback_reason = str(result.get("note", ""))
        elif name in {"flights", "lodging"}:
            booking_id = "bk_flight_out" if name == "flights" else "bk_hotel_tokyo"
            search_result = await search_live_or_fixture(
                name, booking_id, transport=transport, timeout=timeout
            )
            mode, count = search_result.mode, len(search_result.items)
            fallback_reason = search_result.fallback_reason
        else:
            mode, count, fallback_reason = "fixture", 0, SPECS[name].availability
        elapsed = round((time.monotonic() - started) * 1000)
        checks.append({
            "key": name,
            "mode": mode,
            "success": mode in {"live", "sandbox"} or SPECS[name].availability in {
                "designed_fixture", "no_public_api", "approval_required"
            },
            "latency_ms": elapsed,
            "candidate_count": count,
            "error_category": fallback_reason or None,
        })
    return {"checked_at": datetime.now(JST).isoformat(), "checks": checks}


def connector_for(kind: BookingKind) -> str:
    return _OWNERSHIP[kind]


def connector_report() -> List[Dict[str, Any]]:
    return [spec.public() for spec in SPECS.values()]


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

_PREFIX = {
    BookingKind.FLIGHT: "AIR",
    BookingKind.LODGING: "STY",
    BookingKind.ACTIVITY: "ACT",
    BookingKind.DINING: "RES",
    BookingKind.GROUND: "TFR",
}


def execute(item: InventoryItem, *, idempotency_key: str) -> Dict[str, Any]:
    """Perform the booking change. Fixture mode returns the receipt shape the
    real connector would."""
    spec = SPECS[connector_for(item.kind)]
    reference = f"{_PREFIX[item.kind]}-{abs(hash(idempotency_key)) % 900000 + 100000}"
    charged = round(max(item.cost_delta, 0.0), 2) if item.requires_payment else 0.0
    refunded = round(max(-item.cost_delta, 0.0), 2)
    return {
        "ok": True,
        "mode": spec.transaction_mode,
        "endpoint": spec.tools.get(item.action.split(".")[-1], item.action),
        "reference": reference,
        "supplier": item.supplier,
        "supplier_offer_id": item.offer_id,
        "charged": charged,
        "refunded": refunded,
        "reversal": item.drops_booking,
        "idempotency_key": idempotency_key,
        "confirmed_at": datetime.now(JST).isoformat(),
    }


def cancellation_quote(item: InventoryItem, receipt: Dict[str, Any]) -> Dict[str, Any]:
    """Phase one of compensation, ask, do not assume.

    Duffel, LiteAPI and Viator all make you take a quote before you cancel, and
    all three can tell you the refund is less than you paid. That is exactly why
    rollback cannot be modelled as "just undo it".
    """
    charged = float(receipt.get("charged", 0.0))
    spec = SPECS[connector_for(item.kind)]

    # Undoing a *cancellation* is not a refund — it is a fresh purchase, at
    # whatever price the inventory is now, if the inventory still exists. The
    # money the member got back on the way in has to go out again, and the
    # supplier makes no promise the seat, room or dated ticket is still there.
    if receipt.get("reversal") or item.drops_booking:
        outlay = float(receipt.get("refunded", 0.0))
        return {
            "cancellable": False,
            "endpoint": spec.tools.get("book") or spec.tools.get("reserve") or spec.tools.get("modify_reservation"),
            "refund_amount": 0.0,
            "refund_currency": "SGD",
            "refund_to": "none",
            "cancellation_fee": 0.0,
            "unrecoverable": round(outlay, 2),
            "expires_at": (datetime.now(JST) + timedelta(minutes=30)).isoformat(),
            "reinstatement_required": True,
            "note": (
                f"This step gave the booking up. Reversing it means buying it back from "
                f"{item.supplier} at today's price, and the original inventory may already be gone."
            ),
        }

    if item.kind is BookingKind.FLIGHT:
        fee, refundable = 90.0, True
    elif item.kind is BookingKind.LODGING:
        fee, refundable = 0.0, True
    elif item.kind is BookingKind.ACTIVITY:
        fee, refundable = 0.0, True
    elif item.kind is BookingKind.DINING:
        fee, refundable = charged, charged == 0.0
    else:
        fee, refundable = 0.0, True

    refund = max(charged - fee, 0.0)
    return {
        "cancellable": refundable,
        "endpoint": spec.tools.get("quote_cancellation") or spec.tools.get("cancel"),
        "refund_amount": round(refund, 2),
        "refund_currency": "SGD",
        "refund_to": "card" if refund else "none",
        "cancellation_fee": round(fee, 2),
        "unrecoverable": round(charged - refund, 2),
        "expires_at": (datetime.now(JST) + timedelta(minutes=30)).isoformat(),
        "reinstatement_required": False,
        "note": (
            "Full refund to the Card." if refundable and not fee
            else f"Refund net of a {spec.upstream} cancellation fee." if refundable
            else "This supplier will not refund a change made this close to the reservation."
        ),
    }


def confirm_cancellation(item: InventoryItem, quote: Dict[str, Any]) -> Dict[str, Any]:
    """Phase two, Duffel's ``POST /air/order_cancellations/{id}/actions/confirm``
    and its equivalents on the other connectors."""
    spec = SPECS[connector_for(item.kind)]
    return {
        "ok": bool(quote.get("cancellable")),
        "endpoint": spec.tools.get("confirm_cancellation") or spec.tools.get("cancel"),
        "refunded": quote.get("refund_amount", 0.0) if quote.get("cancellable") else 0.0,
        "unrecoverable": quote.get("unrecoverable", 0.0),
        "confirmed_at": datetime.now(JST).isoformat(),
    }
