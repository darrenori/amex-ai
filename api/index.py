"""TripShield API — whole-trip disruption recovery for American Express Card Members.

A single-file FastAPI application. It is the source of truth for the demo's domain
model: the confirmed trip, the disruption, the recovery plans, and the personalized
scoring that decides which plan a given Card Member is shown first.

Deployment
----------
Vercel detects ``api/index.py`` and serves the ASGI ``app`` below as a Python
serverless function. ``vercel.json`` rewrites ``/api/*`` onto it, so every route is
declared with its full ``/api/...`` path.

Local development
-----------------
    uvicorn api.index:app --reload --port 8000

Vite proxies ``/api`` to that port (see ``vite.config.js``).

Everything here is synthetic demonstration data. No booking, payment, cancellation
or claim is real.
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

API = "/api"

app = FastAPI(
    title="TripShield API",
    description=(
        "Whole-trip disruption recovery for American Express Card Members. "
        "Independent AMEX AI Hackathon 2026 concept — synthetic data only."
    ),
    version="0.1.0",
    docs_url=f"{API}/docs",
    openapi_url=f"{API}/openapi.json",
)

# The frontend is served from the same origin in production; permissive CORS only
# matters for local tooling hitting the API directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

CURRENCY = "SGD"

DISCLAIMER = (
    "Illustrative demonstration only. No real purchase, cancellation, refund or "
    "claim is made. Prices and availability are synthetic."
)


# ---------------------------------------------------------------------------
# Domain data
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
    {"merchant": "Tokyo Bay Grand Hotel", "category": "Lodging", "date": "14 Aug", "amount": 1500.00, "status": "posted"},
    {"merchant": "Narita Car Rental", "category": "Car rental", "date": "15 Aug", "amount": 260.00, "status": "pending"},
    {"merchant": "Travel credit", "category": "Statement credit", "date": "16 Aug", "amount": -200.00, "status": "posted"},
]

# One trip, every component on the same Card. That single fact is what lets the
# system price a disruption against the whole journey instead of just the flight.
TRIP = {
    "id": "TRP-2026-0912-SIN-NRT",
    "origin": {"code": "SIN", "city": "Singapore", "airport": "Singapore Changi"},
    "destination": {"code": "NRT", "city": "Tokyo", "airport": "Tokyo Narita"},
    "dates": "12–17 September 2026",
    "nights": 5,
    "cabin": "Business class",
    "currency": CURRENCY,
    "total": 5240,
    "components": [
        {
            "id": "flight",
            "kind": "flight",
            "label": "Flight",
            "title": "SQ638 · 18:05",
            "detail": "Singapore Changi → Tokyo Narita · direct · 7 hours",
            "amount": 3200,
            "status": "cancelled",
            "status_label": "Cancelled",
            "note": "Cancelled 3 hours before departure — mechanical.",
        },
        {
            "id": "hotel",
            "kind": "hotel",
            "label": "Hotel",
            "title": "Tokyo Bay Grand Hotel",
            "detail": f"Check-in 12 Sep · 5 nights at {CURRENCY} 300 · first night non-refundable",
            "amount": 1500,
            "status": "at_risk",
            "status_label": "At risk",
            "note": "The first night is forfeited if arrival slips past tonight.",
        },
        {
            "id": "car",
            "kind": "car",
            "label": "Car rental",
            "title": "Narita counter · 13 Sep 09:00",
            "detail": "5-day booking, collected on arrival",
            "amount": 260,
            "status": "on_track",
            "status_label": "On track",
            "note": "Unaffected while arrival stays within the booking window.",
        },
        {
            "id": "taxes",
            "kind": "fees",
            "label": "Taxes and fees",
            "title": "Taxes, surcharges and fees",
            "detail": "Across flight, hotel and car rental",
            "amount": 280,
            "status": "on_track",
            "status_label": "Confirmed",
            "note": "",
        },
    ],
}

DISRUPTION = {
    "id": "DSR-4471",
    "kind": "flight_cancellation",
    "headline": "Flight SQ638 · Singapore to Tokyo · cancelled",
    "detail": (
        "Cancelled three hours before departure. Your hotel check-in tonight and the "
        "Narita car rental pickup are both connected to this arrival."
    ),
    "arrive_by": "10:00 tomorrow",
    "hotel_deadline_seconds": 45 * 60,
    "hotel_at_risk": 300,
    "emergency_hold_minutes": 90,
}

# fare_delta / hotel_impact / car_impact are all signed against the confirmed trip.
# Negative means the Card Member keeps money; positive means the trip costs more.
PLANS: List[Dict[str, Any]] = [
    {
        "id": "A",
        "key": "same_night",
        "name": "Same-night rebooking",
        "tag": "Protects the trip",
        "objective": "Keep every downstream booking intact",
        "carrier": "Singapore Airlines SQ632",
        "cabin": "Business class",
        "stops": "Direct",
        "stops_status": "good",
        "schedule": "Departs 22:10 · arrives 06:10 tomorrow",
        "arrival": "06:10 tomorrow",
        "hours_lost": 4,
        "fare_delta": 180,
        "hotel_impact": 0,
        "car_impact": 0,
        "reference": "AXV-7K4P2",
        "first_step": "Hold the direct business-class seat departing at 22:10 tonight.",
        "hotel_note": {"status": "good", "text": "Hotel preserved — check-in still makes it"},
        "car_note": {"status": "good", "text": "Car rental pickup unaffected"},
        "notification_clause": (
            "keeping your hotel and car rental intact for {fare_delta} more on the fare"
        ),
        "journey": [
            {"icon": "plane", "tone": "accent", "label": "Step 1 · Rebooking", "title": "SQ632 confirmed",
             "detail": "Business class · direct · departs 22:10, arrives 4 hours later than planned."},
            {"icon": "star", "tone": "good", "label": "Step 2 · Short wait", "title": "Centurion Lounge, Terminal 3",
             "detail": "4 minute walk · priority access, open now.", "included": "Included with your Card"},
            {"icon": "bed", "tone": "good", "label": "Step 3 · Tokyo hotel", "title": "On track",
             "detail": "Late check-in noted with the property — no action needed."},
            {"icon": "car", "tone": "good", "label": "Step 4 · Car rental", "title": "On track",
             "detail": "Narita pickup tomorrow 09:00, unaffected."},
        ],
    },
    {
        "id": "B",
        "key": "next_morning",
        "name": "Next-morning rebooking",
        "tag": "Lower fare · partial loss",
        "objective": "Trade one hotel night for a materially lower fare",
        "carrier": "Japan Airlines JL712",
        "cabin": "Business class",
        "stops": "1 stop · HKG (1h 50m)",
        "stops_status": "warn",
        "schedule": "Departs 08:10 tomorrow · arrives 19:40",
        "arrival": "19:40 tomorrow",
        "hours_lost": 13,
        "fare_delta": -400,
        "hotel_impact": 300,
        "car_impact": 0,
        "reference": "AXT-3M8Q9",
        "first_step": "Hold the one-stop business-class itinerary through Hong Kong.",
        "hotel_note": {"status": "warn", "text": f"First night forfeited — {CURRENCY} 300"},
        "car_note": {"status": "warn", "text": "Pickup rescheduled at no fee"},
        "notification_clause": (
            "saving you {net_abs} overall, even after the forfeited hotel night"
        ),
        "journey": [
            {"icon": "plane", "tone": "accent", "label": "Step 1 · Rebooking", "title": "JL712 confirmed",
             "detail": "Business class · 1 stop via Hong Kong · departs 08:10, arrives 13 hours later than planned."},
            {"icon": "bed", "tone": "accent", "label": "Step 2 · Tonight", "title": "Changi transit hotel",
             "detail": "12 minute walk · overnight block available now.", "included": "Charge to your Card, no new setup"},
            {"icon": "car", "tone": "accent", "label": "Step 3 · Getting there", "title": "Terminal shuttle",
             "detail": "Direct to the transit hotel, every 15 minutes.", "included": "Included with your Card"},
            {"icon": "cup", "tone": "accent", "label": "Step 4 · Evening meal", "title": "Gate-side restaurant",
             "detail": "2 minute walk · open now, seating available.", "included": "Included with your Card"},
            {"icon": "bed", "tone": "warn", "label": "Step 5 · Tokyo hotel", "title": f"Partial loss — {CURRENCY} 300",
             "detail": "First night forfeited; the booking is moved to tomorrow's check-in."},
            {"icon": "car", "tone": "warn", "label": "Step 6 · Car rental", "title": "Rescheduled, no fee",
             "detail": "Pickup pushed to match the new arrival time."},
        ],
    },
    {
        "id": "C",
        "key": "two_day",
        "name": "Two-day rebooking",
        "tag": "Cheapest fare · full loss",
        "objective": "Lowest possible fare, accepting the downstream write-off",
        "carrier": "All Nippon Airways NH860",
        "cabin": "Business class",
        "stops": "Direct",
        "stops_status": "good",
        "schedule": "Departs in 2 days, 06:55 · arrives 15:05",
        "arrival": "15:05 in two days",
        "hours_lost": 48,
        "fare_delta": -1150,
        "hotel_impact": 600,
        "car_impact": 120,
        "reference": "AXE-9N2T6",
        "first_step": "Hold the direct business-class seat departing in two days.",
        "hotel_note": {"status": "critical", "text": f"Two nights forfeited — {CURRENCY} 600"},
        "car_note": {"status": "critical", "text": f"Rebooked at another counter — {CURRENCY} 120 fee"},
        "notification_clause": (
            "the cheapest fare available, though it forfeits two hotel nights and "
            "re-books the car rental"
        ),
        "journey": [
            {"icon": "plane", "tone": "accent", "label": "Step 1 · Rebooking", "title": "NH860 confirmed",
             "detail": "Business class · direct · departs in 2 days at 06:55, arrives 48 hours later than planned."},
            {"icon": "bed", "tone": "accent", "label": "Step 2 · Extended wait", "title": "Airport-area hotel, 2 nights",
             "detail": "8 minute walk · booked for the full wait.", "included": "Charge to your Card, no new setup"},
            {"icon": "cup", "tone": "accent", "label": "Step 3 · Meals", "title": "Nearby dining, both days",
             "detail": "Suggestions refresh daily near your hotel.", "included": "Included with your Card"},
            {"icon": "bed", "tone": "critical", "label": "Step 4 · Tokyo hotel", "title": f"Forfeited — {CURRENCY} 600",
             "detail": "Both nights lost; no longer usable for this trip."},
            {"icon": "car", "tone": "warn", "label": "Step 5 · Car rental", "title": f"Rebooked — {CURRENCY} 120 fee",
             "detail": "New booking made at another Narita counter."},
        ],
    },
]

# The weight is not a setting the traveler picks — it is inferred from what they
# actually chose in the past. The toggle in the UI is an inspection view.
PROFILES: List[Dict[str, Any]] = [
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

PROFILES_BY_ID = {profile["id"]: profile for profile in PROFILES}
PLANS_BY_ID = {plan["id"]: plan for plan in PLANS}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def net_impact(plan: Dict[str, Any]) -> int:
    """Whole-trip financial impact, not just the fare difference."""
    return plan["fare_delta"] + plan["hotel_impact"] + plan["car_impact"]


def money(amount: float) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}{CURRENCY} {abs(round(amount)):,}"


def signed_money(amount: float) -> str:
    sign = "+" if amount >= 0 else "-"
    return f"{sign}{CURRENCY} {abs(round(amount)):,}"


def public_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Plan fields the client needs, plus the derived whole-trip arithmetic."""
    net = net_impact(plan)
    return {
        "id": plan["id"],
        "key": plan["key"],
        "name": plan["name"],
        "tag": plan["tag"],
        "objective": plan["objective"],
        "carrier": plan["carrier"],
        "cabin": plan["cabin"],
        "stops": plan["stops"],
        "stops_status": plan["stops_status"],
        "schedule": plan["schedule"],
        "arrival": plan["arrival"],
        "hours_lost": plan["hours_lost"],
        "fare_delta": plan["fare_delta"],
        "hotel_impact": plan["hotel_impact"],
        "car_impact": plan["car_impact"],
        "net_impact": net,
        "new_total": TRIP["total"] + net,
        "hotel_note": plan["hotel_note"],
        "car_note": plan["car_note"],
        "first_step": plan["first_step"],
        "journey": plan["journey"],
    }


def score_plans(profile_id: str) -> Dict[str, Any]:
    profile = PROFILES_BY_ID.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown profile '{profile_id}'")

    weight = profile["weight"]
    scored = []
    for plan in PLANS:
        net = net_impact(plan)
        time_cost = plan["hours_lost"] * weight
        scored.append(
            {
                "plan": public_plan(plan),
                "net_impact": net,
                "time_cost": time_cost,
                "score": net + time_cost,
                "breakdown": (
                    f"Financial {signed_money(net)}  +  Time {plan['hours_lost']}h "
                    f"× {CURRENCY} {weight}/hr = {money(time_cost)}"
                ),
            }
        )

    ranked = sorted(scored, key=lambda row: row["score"])
    winner, runner_up = ranked[0], ranked[1]
    wp = winner["plan"]
    raw_winner = PLANS_BY_ID[wp["id"]]

    if winner["net_impact"] == 0:
        net_text = "breaks even financially"
    elif winner["net_impact"] < 0:
        net_text = f"nets {money(abs(winner['net_impact']))} saved overall"
    else:
        net_text = f"costs {money(winner['net_impact'])} net"

    gap = round(runner_up["score"] - winner["score"])
    hours = wp["hours_lost"]

    clause = raw_winner["notification_clause"].format(
        fare_delta=money(abs(wp["fare_delta"])),
        net_abs=money(abs(winner["net_impact"])),
    )

    return {
        "profile": profile,
        "currency": CURRENCY,
        "recommended_plan_id": wp["id"],
        "scored": scored,
        "ranked_ids": [row["plan"]["id"] for row in ranked],
        "formula": "Personalized score = whole-trip financial impact + (hours lost × inferred time value). Lower is better.",
        "explanation": (
            f"{wp['name']} is recommended for a {profile['name'].lower()} traveler "
            f"({profile['description']}). It {net_text} once fare, hotel and car rental are "
            f"combined, and gives up {hours} extra hour{'' if hours == 1 else 's'} — a personalized "
            f"score of {money(winner['score'])}, {money(gap)} better than {runner_up['plan']['name']}, "
            f"the next-best option for this profile."
        ),
        "notification": (
            f"Your SIN→NRT flight was cancelled. We recommend {wp['name']} — {clause}. "
            "Every alternative is one tap away."
        ),
    }


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: str = Field(default="", max_length=254)
    password: str = Field(default="", max_length=254)


class ConfirmRequest(BaseModel):
    plan_id: str
    profile_id: str = "time"
    emergency: bool = False


class HoldRequest(BaseModel):
    plan_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get(f"{API}/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "tripshield-api", "version": app.version}


@app.post(f"{API}/auth/login")
def login(payload: LoginRequest) -> Dict[str, Any]:
    """Demo credential check. Nothing here is a real authentication system."""
    email = payload.email.strip().lower()
    if email != DEMO_CREDENTIALS["email"] or payload.password != DEMO_CREDENTIALS["password"]:
        raise HTTPException(
            status_code=401,
            detail="Those demo credentials do not match. Use the details shown below the form.",
        )
    return {
        "token": f"demo_{secrets.token_urlsafe(16)}",
        "member": MEMBER,
        "disclaimer": DISCLAIMER,
    }


@app.get(f"{API}/account")
def account() -> Dict[str, Any]:
    return {
        "member": MEMBER,
        "currency": CURRENCY,
        "transactions": TRANSACTIONS,
        "benefits": BENEFITS,
        "trip": TRIP,
        "disclaimer": DISCLAIMER,
    }


@app.get(f"{API}/trip")
def trip() -> Dict[str, Any]:
    return {"trip": TRIP, "currency": CURRENCY}


@app.get(f"{API}/benefits")
def benefits() -> Dict[str, Any]:
    return {"benefits": BENEFITS, "travel_credit": MEMBER["travel_credit"], "currency": CURRENCY}


@app.get(f"{API}/profiles")
def profiles() -> Dict[str, Any]:
    return {"profiles": PROFILES, "currency": CURRENCY}


@app.post(f"{API}/disruption/simulate")
def simulate_disruption(profile_id: str = "time") -> Dict[str, Any]:
    """Fire the cancellation and return the scored recovery set in one round trip."""
    result = score_plans(profile_id)
    return {
        "disruption": DISRUPTION,
        "trip": TRIP,
        "currency": CURRENCY,
        "recovery": result,
        "disclaimer": DISCLAIMER,
    }


@app.get(f"{API}/recovery")
def recovery(profile_id: str = "time") -> Dict[str, Any]:
    return {"currency": CURRENCY, "recovery": score_plans(profile_id), "disclaimer": DISCLAIMER}


@app.post(f"{API}/recovery/hold")
def hold_seat(payload: HoldRequest) -> Dict[str, Any]:
    plan_id = payload.plan_id or "A"
    plan = PLANS_BY_ID.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Unknown plan '{plan_id}'")
    minutes = DISRUPTION["emergency_hold_minutes"]
    return {
        "plan_id": plan_id,
        "held_for_minutes": minutes,
        "message": (
            f"A {plan['cabin'].lower()} seat on {plan['carrier']} is held for {minutes} minutes "
            "while you decide. Your cabin preference is maintained."
        ),
        "disclaimer": DISCLAIMER,
    }


@app.post(f"{API}/recovery/confirm")
def confirm_plan(payload: ConfirmRequest) -> Dict[str, Any]:
    plan = PLANS_BY_ID.get(payload.plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Unknown plan '{payload.plan_id}'")

    result = score_plans(payload.profile_id)
    net = net_impact(plan)
    followed_recommendation = payload.plan_id == result["recommended_plan_id"]

    return {
        "confirmed": True,
        "plan_id": plan["id"],
        "title": (
            "Emergency replacement activated."
            if payload.emergency
            else f"{plan['name']} confirmed."
        ),
        "message": (
            f"{plan['name']} · {plan['schedule']} · reference {plan['reference']}. "
            f"Revised trip total {money(TRIP['total'] + net)} "
            f"({signed_money(net)} against the confirmed trip). "
            "This demonstration made no real booking change."
        ),
        "reference": plan["reference"],
        "new_total": TRIP["total"] + net,
        "net_impact": net,
        "followed_recommendation": followed_recommendation,
        "journey": plan["journey"],
        "disclaimer": DISCLAIMER,
    }
