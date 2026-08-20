"""The demonstration booking history — the orchestrator's single source of truth.

Because every component of this trip was charged to the same Card, the platform
can answer ``GET /user/bookings`` for the whole journey instead of asking each
supplier separately. That one fact is what makes the dependency graph buildable.

Grounding
---------
Carriers, routes, properties and attractions are real and really do connect the
way they are modelled here (SQ flies SIN–NRT; the Narita Express runs NRT into
Tokyo; Jetstar Japan flies NRT–KIX; Hilton Tokyo Bay is the Maihama resort hotel
beside Tokyo Disney Resort; Hotel Granvia Osaka sits inside Osaka Station).

Flight numbers, prices, seat availability, references and the member are
synthetic. No booking here exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from .domain import Booking, BookingKind, Dependency, DependencyType, Flexibility, Severity

SGT = timezone(timedelta(hours=8))    # Singapore, no DST
JST = timezone(timedelta(hours=9))    # Japan, no DST

CURRENCY = "SGD"

DISCLAIMER = (
    "Illustrative demonstration only. No real purchase, cancellation, refund or "
    "claim is made. Prices, availability and references are synthetic."
)


def sgt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=SGT)


def jst(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=JST)


# ---------------------------------------------------------------------------
# Member
# ---------------------------------------------------------------------------

DEMO_CREDENTIALS = {"email": "demo@amextravel.com", "password": "Travel123!"}

MEMBER = {
    "name": "Alex Tan",
    "first_name": "Alex",
    "greeting": "Welcome back",
    "member_since": "2014",
    "tier": "Platinum",
    "card_label": "The Platinum Card®",
    "card_last_four": "1005",
    "rewards_points": 128450,
    "travel_credit": 200,
}

BENEFITS = [
    {
        "id": "lounge",
        "initial": "L",
        "title": "Global Lounge Collection access",
        "detail": "Centurion and partner lounges at Changi and Narita, included for this trip.",
    },
    {
        "id": "credit",
        "initial": "$",
        "title": f"{CURRENCY} 200 travel credit available",
        "detail": "Applies automatically to an eligible prepaid travel booking.",
    },
    {
        "id": "fhr",
        "initial": "H",
        "title": "Fine Hotels + Resorts®",
        "detail": "Premium stays with room upgrades and daily breakfast at participating properties.",
    },
    {
        "id": "protection",
        "initial": "P",
        "title": "Trip cancellation and interruption protection",
        "detail": "Eligible non-refundable spend on this itinerary is covered when disruption occurs.",
    },
]

TRANSACTIONS = [
    {"merchant": "Singapore Airlines", "category": "Airline", "date": "14 Aug", "amount": 3200.00, "status": "posted"},
    {"merchant": "Hilton Tokyo Bay", "category": "Lodging", "date": "14 Aug", "amount": 900.00, "status": "posted"},
    {"merchant": "Hotel Granvia Osaka", "category": "Lodging", "date": "14 Aug", "amount": 560.00, "status": "posted"},
    {"merchant": "Jetstar Japan", "category": "Airline", "date": "15 Aug", "amount": 190.00, "status": "pending"},
    {"merchant": "Tokyo Disney Resort", "category": "Attraction", "date": "15 Aug", "amount": 80.00, "status": "posted"},
    {"merchant": "Travel credit", "category": "Statement credit", "date": "16 Aug", "amount": -200.00, "status": "posted"},
]


# ---------------------------------------------------------------------------
# The itinerary
# ---------------------------------------------------------------------------

def build_bookings() -> Dict[str, Booking]:
    """Fresh objects on every call — the graph mutates status in place."""

    bookings = [
        Booking(
            id="bk_flight_out",
            kind=BookingKind.FLIGHT,
            label="Outbound flight",
            title="SQ638 · Singapore → Tokyo Narita",
            detail="Business class · direct · departs 18 Sep 09:00 SGT, arrives 17:05 JST",
            supplier="Singapore Airlines",
            supplier_ref="ord_0000ASQ638Xk9",
            connector="flights",
            start=sgt(18, 9, 0),
            end=jst(18, 17, 5),
            location="Singapore Changi T3 → Narita T1",
            place_code="SIN-NRT",
            amount=3200.0,
            refundable=2800.0,
            flexibility=Flexibility.REBOOKABLE,
            note="Every downstream booking on this trip hangs off this arrival.",
            meta={"flight_number": "SQ638", "carrier_iata": "SQ", "cabin": "business"},
        ),
        Booking(
            id="bk_transfer_nex",
            kind=BookingKind.GROUND,
            label="Airport transfer",
            title="Narita Express · NRT → Tokyo Station",
            detail="Reserved Green Car seat, 18 Sep 18:15 departure",
            supplier="JR East",
            supplier_ref="jre_NEX_4471882",
            connector="ground",
            start=jst(18, 18, 15),
            end=jst(18, 19, 20),
            location="Narita Airport T1 → Tokyo Station",
            place_code="NRT-TYO",
            amount=60.0,
            refundable=60.0,
            flexibility=Flexibility.REBOOKABLE,
            note="Seat reservation is tied to a departure time.",
            meta={"service": "N'EX 46"},
        ),
        Booking(
            id="bk_hotel_tokyo",
            kind=BookingKind.LODGING,
            label="Tokyo hotel",
            title="Hilton Tokyo Bay · 3 nights",
            detail=f"Check-in 18 Sep from 15:00 · 3 nights at {CURRENCY} 300 · first night non-refundable",
            supplier="Hilton Tokyo Bay",
            supplier_ref="lit_bk_9QR41K7",
            connector="lodging",
            start=jst(18, 15, 0),
            end=jst(21, 11, 0),
            location="Maihama, Urayasu",
            place_code="TYO",
            amount=900.0,
            refundable=600.0,
            flexibility=Flexibility.SHIFTABLE,
            note="Guaranteed for late arrival until 02:00 — after that the night is gone.",
            meta={
                "nights": 3,
                "rate_per_night": 300.0,
                # A prepaid, guaranteed booking at a property with 24-hour
                # reception is held past midnight; it is not held indefinitely.
                # This is the number that decides whether a same-night rebooking
                # saves the first night or forfeits it, so it is stated once here
                # rather than assumed anywhere else.
                "latest_check_in": jst(19, 2, 0).isoformat(),
                "non_refundable_first_night": 300.0,
            },
        ),
        Booking(
            id="bk_dining_tokyo",
            kind=BookingKind.DINING,
            label="Dinner reservation",
            title="Sushi counter, Shinjuku · 20:00",
            detail="Two seats at the counter, 18 Sep · deposit taken",
            supplier="TableCheck",
            supplier_ref="tc_res_88213",
            connector="dining",
            start=jst(18, 20, 0),
            end=jst(18, 21, 30),
            location="Shinjuku, Tokyo",
            place_code="TYO",
            amount=180.0,
            refundable=0.0,
            flexibility=Flexibility.FIXED,
            note="Deposit is forfeited on a same-day no-show.",
            meta={"party_size": 2},
        ),
        Booking(
            id="bk_activity_tdl",
            kind=BookingKind.ACTIVITY,
            label="Attraction",
            title="Tokyo Disneyland · 1-Day Passport",
            detail="Dated entry, 19 Sep · park opens 09:00",
            supplier="Tokyo Disney Resort",
            supplier_ref="BR-556320117",
            connector="activities",
            start=jst(19, 9, 0),
            end=jst(19, 20, 0),
            location="Maihama, Urayasu",
            place_code="TYO",
            amount=80.0,
            refundable=80.0,
            flexibility=Flexibility.FIXED,
            note="A dated passport is valid only for the date printed on it.",
            meta={"date_locked": True},
        ),
        Booking(
            id="bk_flight_dom",
            kind=BookingKind.FLIGHT,
            label="Domestic flight",
            title="GK205 · Tokyo Narita → Osaka Kansai",
            detail="Departs 21 Sep 14:00, arrives 15:40",
            supplier="Jetstar Japan",
            supplier_ref="ord_0000AGK205Pm2",
            connector="flights",
            start=jst(21, 14, 0),
            end=jst(21, 15, 40),
            location="Narita T3 → Kansai T1",
            place_code="NRT-KIX",
            amount=190.0,
            refundable=0.0,
            flexibility=Flexibility.REBOOKABLE,
            note="Low-cost fare, non-refundable but changeable for a fee.",
            meta={"flight_number": "GK205", "carrier_iata": "GK", "cabin": "economy", "change_fee": 55.0},
        ),
        Booking(
            id="bk_hotel_osaka",
            kind=BookingKind.LODGING,
            label="Osaka hotel",
            title="Hotel Granvia Osaka · 2 nights",
            detail=f"Check-in 21 Sep from 16:00 · 2 nights at {CURRENCY} 280 · free cancellation",
            supplier="Hotel Granvia Osaka",
            supplier_ref="lit_bk_2XT80M4",
            connector="lodging",
            start=jst(21, 16, 0),
            end=jst(23, 11, 0),
            location="Umeda, Osaka",
            place_code="OSA",
            amount=560.0,
            refundable=560.0,
            flexibility=Flexibility.SHIFTABLE,
            note="Fully refundable up to 24 hours before check-in.",
            meta={
                "nights": 2,
                "rate_per_night": 280.0,
                "latest_check_in": jst(21, 23, 59).isoformat(),
                "non_refundable_first_night": 0.0,
            },
        ),
    ]
    return {b.id: b for b in bookings}


def build_dependencies() -> List[Dependency]:
    """The edges. Severity is the interesting column: a violated HARD edge
    invalidates the target, a violated SOFT edge only degrades it."""

    return [
        Dependency(
            source="bk_flight_out", target="bk_transfer_nex",
            type=DependencyType.TEMPORAL, severity=Severity.HARD,
            min_buffer=timedelta(minutes=60),
            rationale="Immigration, baggage reclaim and customs at Narita before the platform.",
        ),
        Dependency(
            source="bk_transfer_nex", target="bk_hotel_tokyo",
            type=DependencyType.SPATIAL, severity=Severity.HARD,
            min_buffer=timedelta(minutes=45),
            rationale="The transfer is how the member physically reaches the property.",
        ),
        Dependency(
            source="bk_flight_out", target="bk_dining_tokyo",
            type=DependencyType.TEMPORAL, severity=Severity.SOFT,
            min_buffer=timedelta(minutes=150),
            rationale="Narita to Shinjuku, with time to drop bags. Missing dinner costs the deposit, not the trip.",
        ),
        Dependency(
            source="bk_flight_out", target="bk_activity_tdl",
            type=DependencyType.TEMPORAL, severity=Severity.HARD,
            min_buffer=timedelta(hours=10),
            rationale=(
                "A dated park passport cannot be used before the member is in the country. "
                "Ten hours covers landing, immigration, the run to Maihama and a night's sleep."
            ),
        ),
        Dependency(
            source="bk_hotel_tokyo", target="bk_activity_tdl",
            type=DependencyType.SPATIAL, severity=Severity.SOFT,
            min_buffer=timedelta(minutes=0),
            rationale="The park is a shuttle ride from the property; another base only costs time.",
        ),
        Dependency(
            source="bk_activity_tdl", target="bk_flight_dom",
            type=DependencyType.TEMPORAL, severity=Severity.HARD,
            min_buffer=timedelta(hours=3),
            rationale="Maihama to Narita T3 plus the low-cost carrier's check-in cut-off.",
        ),
        Dependency(
            source="bk_flight_dom", target="bk_hotel_osaka",
            type=DependencyType.TEMPORAL, severity=Severity.HARD,
            min_buffer=timedelta(minutes=90),
            rationale="Kansai to Umeda before the front desk cut-off.",
        ),
    ]


TRIP_META = {
    "id": "TRP-2026-0918-SIN-NRT-KIX",
    "dates": "18–23 September 2026",
    "origin": {"code": "SIN", "city": "Singapore", "airport": "Singapore Changi"},
    "destination": {"code": "NRT", "city": "Tokyo", "airport": "Tokyo Narita"},
    "onward": {"code": "KIX", "city": "Osaka", "airport": "Kansai International"},
    "nights": 5,
    "cabin": "Business class",
    "currency": CURRENCY,
    "taxes_and_fees": 280.0,
}


# ---------------------------------------------------------------------------
# Inferred time-value profiles
# ---------------------------------------------------------------------------

# The weight is never a setting the member picks. It is regressed from what they
# actually chose when they had a cost/time trade-off in front of them. The
# selector in the UI is an inspection view for reviewers.
PROFILES: List[Dict] = [
    {
        "id": "time",
        "name": "Time-sensitive",
        "description": "pays a premium to save hours",
        "weight": 45,
        "icon": "clock",
        "history": [
            {"when": "6 weeks ago", "text": f"Paid {CURRENCY} 130 more for a direct flight instead of a cheaper option with a 5-hour-longer layover."},
            {"when": "3 months ago", "text": "Rebooked the same evening after a delay instead of waiting for a cheaper next-day fare."},
        ],
    },
    {
        "id": "balanced",
        "name": "Balanced",
        "description": "weighs cost and time evenly",
        "weight": 25,
        "icon": "scale",
        "history": [
            {"when": "2 months ago", "text": f"Chose a flight 2 hours longer to save {CURRENCY} 90 on the fare."},
            {"when": "5 months ago", "text": f"Paid {CURRENCY} 60 more than the cheapest option to avoid an overnight layover."},
        ],
    },
    {
        "id": "cost",
        "name": "Cost-sensitive",
        "description": "waits longer to save money",
        "weight": 8,
        "icon": "coin",
        "history": [
            {"when": "1 month ago", "text": f"Chose an itinerary 8 hours longer to save {CURRENCY} 150 on the fare."},
            {"when": "4 months ago", "text": "Waited two days for a fare drop rather than book immediately."},
        ],
    },
]

PROFILES_BY_ID = {p["id"]: p for p in PROFILES}


def trip_total(bookings: Dict[str, Booking]) -> float:
    return sum(b.amount for b in bookings.values()) + TRIP_META["taxes_and_fees"]
