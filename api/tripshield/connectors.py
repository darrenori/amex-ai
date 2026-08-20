"""MCP-style clients onto real travel APIs.

Each connector is the standardized interface an agent gets to one external
capability. They are declared against real, currently-available products, and
every tool name below maps to a real endpoint on that product:

``status``      AeroDataBox — flight status, the detection trigger.
                GET /flights/number/{number}/{date}
                Status vocabulary is theirs verbatim: Expected, EnRoute, CheckIn,
                Boarding, GateClosed, Departed, Delayed, Approaching, Arrived,
                Canceled, Diverted, CanceledUncertain, Unknown.
                https://doc.aerodatabox.com/

``flights``     Duffel — search, change and cancel air bookings. Its two-phase
                cancellation (quote first, confirm second) is exactly the shape
                compensation needs.
                POST /air/offer_requests, POST /air/order_change_requests,
                POST /air/order_changes/{id}/actions/confirm,
                POST /air/order_cancellations  -> refund_amount, refund_to, expires_at
                POST /air/order_cancellations/{id}/actions/confirm
                https://duffel.com/docs/api/order-cancellations

``lodging``     LiteAPI (Nuitée) — 2M+ properties behind one REST interface, with
                a free sandbox that mirrors production.
                POST /rates, POST /rates/prebook (locks the price briefly),
                POST /rates/book, PUT /bookings/{id}/cancel
                https://docs.liteapi.travel/reference/overview

``activities``  Viator Partner API — attraction inventory, and the same
                quote-then-cancel split.
                POST /availability/check, POST /bookings/book,
                POST /bookings/cancel-quote, POST /bookings/cancel
                https://docs.viator.com/partner-api/technical/

``dining``      TableCheck — the reservation platform most Japanese restaurants
                in this price band actually run on.

``ground``      JR East / airport transfer inventory.

Live vs. fixture
----------------
``status`` will call AeroDataBox for real when ``AERODATABOX_API_KEY`` is set,
because reading a flight's status is free, read-only and the genuinely useful
thing to prove. Everything else runs against the fixture inventory below:
a demonstration must not transact against real booking APIs.

``connector_report()`` renders this table into the UI so the wiring is visible
rather than claimed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .catalog import JST, SGT, jst, sgt
from .domain import BookingKind

# AeroDataBox's own status vocabulary. `Canceled` (one l) is their spelling.
CANCELLING_STATUSES = {"Canceled", "CanceledUncertain"}
DISRUPTIVE_STATUSES = CANCELLING_STATUSES | {"Diverted"}


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
    mode: str = "fixture"                 # "live" once the key is present

    def public(self) -> Dict[str, Any]:
        live = bool(os.environ.get(self.auth_env)) and self.mode == "live"
        return {
            "key": self.key,
            "server": self.server,
            "upstream": self.upstream,
            "base_url": self.base_url,
            "docs": self.docs,
            "auth_env": self.auth_env,
            "tools": self.tools,
            "mode": "live" if live else "fixture",
            "credential_present": bool(os.environ.get(self.auth_env)),
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
    mode="live",
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
            "reason": "Aircraft technical — inbound rotation withdrawn",
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


def fetch_flight_status(flight_number: str, date_local: str) -> Dict[str, Any]:
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
            with urllib.request.urlopen(request, timeout=6) as response:
                payload = json.loads(response.read().decode("utf-8"))
            legs = payload if isinstance(payload, list) else [payload]
            if legs:
                leg = legs[0]
                return {
                    "source": "live",
                    "upstream": STATUS_SPEC.upstream,
                    "endpoint": f"GET /flights/number/{compact}/{date_local}",
                    "flight": leg,
                    "status": leg.get("status", "Unknown"),
                    "cancelled": leg.get("status") in CANCELLING_STATUSES,
                }
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as error:
            # A monitor that falls over when the upstream blips is not a monitor.
            return _fixture_status(compact, date_local, note=f"live lookup failed ({type(error).__name__}); using fixture")

    return _fixture_status(compact, date_local)


def _fixture_status(compact: str, date_local: str, note: str = "") -> Dict[str, Any]:
    leg = FIXTURE_STATUS.get(compact)
    if leg is None:
        return {
            "source": "fixture",
            "upstream": STATUS_SPEC.upstream,
            "endpoint": f"GET /flights/number/{compact}/{date_local}",
            "flight": None,
            "status": "Unknown",
            "cancelled": False,
            "note": note or "No fixture for this flight number.",
        }
    return {
        "source": "fixture",
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
    meta: Dict[str, Any] = field(default_factory=dict)


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
)

DINING_SPEC = ConnectorSpec(
    key="dining",
    server="mcp/tablecheck-dining",
    upstream="TableCheck",
    base_url="https://api.tablecheck.com/v2",
    docs="https://www.tablecheck.com/en/join/",
    auth_env="TABLECHECK_API_KEY",
    tools={
        "search_availability": "GET /shops/{shop}/availability",
        "modify_reservation": "PATCH /reservations/{id}",
        "cancel_reservation": "DELETE /reservations/{id}",
    },
)

GROUND_SPEC = ConnectorSpec(
    key="ground",
    server="mcp/ground-transfer",
    upstream="JR East / airport transfer desk",
    base_url="https://api.jreast.example/transfers",
    docs="https://www.jreast.co.jp/multi/en/nex/",
    auth_env="GROUND_TRANSFER_KEY",
    tools={
        "search": "GET /connections",
        "reserve": "POST /reservations",
        "cancel": "DELETE /reservations/{id}",
    },
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
        ),
        InventoryItem(
            id="opt_flt_nh802", offer_id="off_0000ANH802Qb7",
            title="NH802 · earlier same-day direct",
            detail="Business class · direct · departs 11:30 SGT, arrives 19:35 JST",
            supplier="All Nippon Airways",
            start=sgt(18, 11, 30), end=jst(18, 19, 35),
            cost_delta=320.0, quality=0.97,
            notes=["Shortest delay available on the day.",
                   "Star Alliance — status and lounge access carry over."],
            meta={"hours_lost": 2.5, "stops": "Direct", "stops_status": "good", "cabin": "Business class"},
            **common,
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
        ),
        InventoryItem(
            id="opt_lod_transit", offer_id="rate_new_CHG_TRANSIT",
            title="Add a Changi transit hotel for tonight",
            detail="Aerotel Singapore, airside · overnight block while waiting for the morning departure",
            supplier="Aerotel Singapore",
            start=sgt(18, 22, 0), end=sgt(19, 6, 30),
            cost_delta=90.0, changes_booking=True, quality=0.80,
            notes=["Airside, so there is no immigration round-trip before the 08:10 departure.",
                   "Charged to the same Card — no new payment method to set up."],
            meta={"supplementary": True},
            booking_id="bk_hotel_tokyo_supplement",
            kind=BookingKind.LODGING,
            location="Changi Airport T1, airside",
            place_code="SIN",
            action="lodging.book",
            compensating_action="lodging.cancel",
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
        ),
        InventoryItem(
            id="opt_din_cancel", offer_id="tc_cancel_88213",
            title="Cancel — deposit is forfeited",
            detail="Same-day cancellation, no refund of the deposit",
            supplier="TableCheck",
            start=jst(18, 20, 0), end=jst(18, 20, 0),
            cost_delta=180.0, changes_booking=True, quality=0.2,
            notes=["A same-day cancellation forfeits the full deposit."],
            requires_payment=False,
            drops_booking=True,
            **common,
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
        ),
        InventoryItem(
            id="opt_gnd_nex_late", offer_id="jre_NEX_late",
            title="Move to the 23:00 Narita Express",
            detail="Last N'EX of the night · seat reservation re-issued at no fare difference",
            supplier="JR East",
            start=jst(18, 23, 0), end=jst(19, 0, 5),
            cost_delta=8.0, changes_booking=True, quality=0.82,
            notes=["Last departure — a further slip means a road transfer."],
            **common,
        ),
        InventoryItem(
            id="opt_gnd_private", offer_id="tfr_private_NRT",
            title="Private car from Narita",
            detail="Meet-and-greet at arrivals, direct to the property, any hour",
            supplier="Airport transfer desk",
            start=jst(18, 22, 40), end=jst(18, 23, 55),
            cost_delta=75.0, changes_booking=True, quality=0.95,
            notes=["Immune to the rail timetable — the reason it is worth 75 on a late arrival."],
            **common,
        ),
        InventoryItem(
            id="opt_gnd_next_day", offer_id="jre_NEX_next",
            title="Re-issue for the next arrival day",
            detail="Seat reservation moved to match the new flight",
            supplier="JR East",
            start=jst(19, 20, 45), end=jst(19, 21, 50),
            cost_delta=0.0, changes_booking=True, quality=0.85,
            **common,
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
        ),
    ]


INVENTORY: List[InventoryItem] = (
    _flight_inventory()
    + _lodging_inventory()
    + _activity_inventory()
    + _dining_inventory()
    + _ground_inventory()
    + _domestic_inventory()
)

INVENTORY_BY_ID: Dict[str, InventoryItem] = {item.id: item for item in INVENTORY}


def search(connector: str, booking_id: str) -> List[InventoryItem]:
    """The one read every agent makes. Real connectors would send the task's
    constraints upstream; the fixture filters the same shape locally."""
    return [
        item for item in INVENTORY
        if item.booking_id in (booking_id, f"{booking_id}_supplement")
        and SPECS[connector].key == connector
        and _owns(connector, item)
    ]


_OWNERSHIP = {
    BookingKind.FLIGHT: "flights",
    BookingKind.LODGING: "lodging",
    BookingKind.ACTIVITY: "activities",
    BookingKind.DINING: "dining",
    BookingKind.GROUND: "ground",
}


def _owns(connector: str, item: InventoryItem) -> bool:
    return _OWNERSHIP[item.kind] == connector


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
        "mode": spec.public()["mode"],
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
    """Phase one of compensation — ask, do not assume.

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
    """Phase two — Duffel's ``POST /air/order_cancellations/{id}/actions/confirm``
    and its equivalents on the other connectors."""
    spec = SPECS[connector_for(item.kind)]
    return {
        "ok": bool(quote.get("cancellable")),
        "endpoint": spec.tools.get("confirm_cancellation") or spec.tools.get("cancel"),
        "refunded": quote.get("refund_amount", 0.0) if quote.get("cancellable") else 0.0,
        "unrecoverable": quote.get("unrecoverable", 0.0),
        "confirmed_at": datetime.now(JST).isoformat(),
    }
