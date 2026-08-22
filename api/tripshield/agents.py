"""Specialized recovery subagents.

One agent per capability, not one per booking. A single Accommodation Agent that
understands check-in cut-offs, forfeited nights and rate rules is worth more than
seven identical agents that each happen to be pointed at a different reservation.

Every agent takes the same contract — a :class:`RecoveryTask` carrying an
objective, hard constraints, its dependencies and the tools it is allowed to
call — and returns *several* options rather than one answer. Choosing between
them is the orchestrator's job, and ultimately the member's.

Relationship to the model agents
--------------------------------
The filtering below is deterministic on purpose: a park passport whose date has
passed is not a judgement call. After this layer returns known feasible option
IDs, ``ai_agents.py`` runs bounded specialty assessments. These classes remain
the complete fallback when a model is unavailable or returns invalid output.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from . import connectors
from .connectors import InventoryItem
from .domain import BookingKind, Flexibility, Option, RecoveryTask, TaskState
from .graph import Itinerary


class RecoveryAgent:
    """Base class. Subclasses supply ``feasible`` and ``rank``."""

    name = "agent"
    connector = ""
    handles: Sequence[BookingKind] = ()

    async def run(
        self,
        task: RecoveryTask,
        itinerary: Itinerary,
        *,
        search_cache: Optional[Dict[Tuple[str, str], connectors.SearchResult]] = None,
    ) -> List[Option]:
        task.state = TaskState.RUNNING
        booking = itinerary.bookings.get(task.booking_id)

        result = await connectors.search_live_or_fixture(
            self.connector,
            task.booking_id,
            task=task,
            itinerary=itinerary,
            cache=search_cache,
        )
        items = result.items
        task.log.append(
            f"{self.connector}.{list(connectors.SPECS[self.connector].tools)[0]} "
            f"→ {len(items)} candidates ({result.mode})"
        )
        if result.fallback_reason:
            task.log.append(f"{result.upstream} fallback: {result.fallback_reason}")

        kept: List[InventoryItem] = []
        for item in items:
            verdict = self.feasible(item, task, itinerary)
            if verdict is True:
                kept.append(item)
            else:
                task.log.append(f"ruled out {item.id}: {verdict}")

        # A little real concurrency, so the orchestrator's fan-out is not a lie.
        await asyncio.sleep(0)

        kept.sort(key=lambda item: self.rank(item, task))
        top = kept[: task.constraints.get("max_options", 4)]

        options = [self._to_option(item, task) for item in top]
        for option in options:
            option.source_mode = option.source_mode or result.mode
            option.source_upstream = option.source_upstream or result.upstream
            option.source_note = option.source_note or result.fallback_reason
        task.option_ids = [option.id for option in options]
        task.state = TaskState.COMPLETE if options else TaskState.FAILED
        if not options:
            task.log.append("no option satisfied the constraints")
        return options

    # -- hooks -------------------------------------------------------------

    def feasible(self, item: InventoryItem, task: RecoveryTask, itinerary: Itinerary):
        """Return ``True``, or a short string explaining the rejection."""
        max_extra = task.constraints.get("max_extra_cost")
        if max_extra is not None and item.cost_delta > max_extra:
            return f"costs {item.cost_delta:.0f}, over the {max_extra:.0f} ceiling"
        return True

    def rank(self, item: InventoryItem, task: RecoveryTask) -> tuple:
        priority = task.constraints.get("priority", "balanced")
        if priority == "cost":
            return (item.cost_delta, -item.quality)
        if priority == "time":
            return (item.meta.get("hours_lost", 0.0), item.cost_delta)
        if priority == "disruption":
            return (int(item.changes_booking), -item.quality, item.cost_delta)
        return (-item.quality + item.cost_delta / 1000.0, item.cost_delta)

    # -- shared ------------------------------------------------------------

    def _to_option(self, item: InventoryItem, task: RecoveryTask) -> Option:
        spec = connectors.SPECS[self.connector]
        tool = item.action.split(".")[-1]
        return Option(
            id=item.id,
            task_id=task.id,
            booking_id=item.booking_id,
            kind=item.kind,
            agent=self.name,
            connector=self.connector,
            title=item.title,
            detail=item.detail,
            supplier=item.supplier,
            supplier_offer_id=item.offer_id,
            start=item.start,
            end=item.end,
            location=item.location,
            place_code=item.place_code,
            cost_delta=item.cost_delta,
            hours_lost=float(item.meta.get("hours_lost", 0.0)),
            changes_booking=item.changes_booking,
            quality=item.quality,
            drops_booking=item.drops_booking,
            optional=bool(item.meta.get("supplementary")),
            reliability_risk=item.reliability_risk,
            links=list(item.links),
            notes=list(item.notes),
            tool_call=f"{spec.adapter} · {spec.tools.get(tool, item.action)}",
            source_mode=getattr(item, "source_mode", "fixture"),
            source_upstream=getattr(item, "upstream", "") or spec.upstream,
            source_note=getattr(item, "source_note", ""),
            amex_partner=getattr(item, "amex_partner", None),
            synthetic=getattr(item, "synthetic", True),
            inventory_snapshot=connectors.inventory_item_snapshot(item),
        )


# ---------------------------------------------------------------------------
# Flight
# ---------------------------------------------------------------------------

class FlightRecoveryAgent(RecoveryAgent):
    """Replaces the cancelled leg. Runs first, because its arrival time is the
    input every other agent's task is built from."""

    name = "Flight Agent"
    connector = "flights"
    handles = (BookingKind.FLIGHT,)

    def feasible(self, item, task, itinerary):
        base = super().feasible(item, task, itinerary)
        if base is not True:
            return base

        not_before = task.constraints.get("not_before")
        if not_before and item.start < datetime.fromisoformat(not_before):
            return "departs before the member could reach the airport"

        arrive_before = task.constraints.get("arrive_before")
        if arrive_before and item.end > datetime.fromisoformat(arrive_before):
            # Deliberately not a rejection when the constraint is advisory: an
            # option that misses the target arrival but saves SGD 1,150 is still
            # a real choice, and hiding it would be the system deciding for the
            # member. It is rejected only when the caller marks it binding.
            if task.constraints.get("arrive_before_binding"):
                return f"arrives {item.end:%d %b %H:%M}, past the required arrival"
        return True

    def rank(self, item, task):
        # Deterministic fallback for the model-backed flight assessment.
        priority = task.constraints.get("priority", "balanced")
        if priority == "time":
            return (item.meta.get("hours_lost", 0.0), item.cost_delta)
        if priority == "cost":
            return (item.cost_delta, item.meta.get("hours_lost", 0.0))
        if priority == "disruption":
            return (item.meta.get("hours_lost", 0.0), item.cost_delta)
        return (
            item.cost_delta / 400.0 + item.meta.get("hours_lost", 0.0) / 12.0 - item.quality,
            item.cost_delta,
        )


# ---------------------------------------------------------------------------
# Accommodation
# ---------------------------------------------------------------------------

class AccommodationRecoveryAgent(RecoveryAgent):
    """Understands check-in cut-offs, non-refundable nights and supplementary
    stays for an overnight wait."""

    name = "Accommodation Agent"
    connector = "lodging"
    handles = (BookingKind.LODGING,)

    def feasible(self, item, task, itinerary):
        base = super().feasible(item, task, itinerary)
        if base is not True:
            return base

        arrival = task.constraints.get("arrival")
        if not arrival:
            return True
        arrival_at = datetime.fromisoformat(arrival)
        buffer = timedelta(minutes=task.constraints.get("arrival_buffer_minutes", 45))
        reachable = arrival_at + buffer

        if item.meta.get("supplementary"):
            # A transit hotel is for a night, not for an afternoon. The test is
            # whether the replacement departs on a *later day* — a seven-hour
            # wait that ends at 13:45 is a long lunch, not an overnight.
            departure = task.constraints.get("replacement_departure")
            if not departure:
                return "no overnight wait to cover"
            disrupted_at = datetime.fromisoformat(task.constraints["disrupted_at"])
            departs_at = datetime.fromisoformat(departure)
            if departs_at.date() <= disrupted_at.date():
                return "the replacement leaves the same day — there is no night to cover"
            if departs_at - disrupted_at < timedelta(hours=8):
                return f"the wait is only {(departs_at - disrupted_at).total_seconds() / 3600:.1f}h"
            return True

        cutoff = task.constraints.get("latest_check_in")
        if cutoff and item.id == "opt_lod_keep" and reachable > datetime.fromisoformat(cutoff):
            return f"reachable at {reachable:%d %b %H:%M}, past the {datetime.fromisoformat(cutoff):%H:%M} desk cut-off"

        if item.start.date() < reachable.date():
            return f"check-in date {item.start:%d %b} is before the member can arrive"
        if item.start.date() > reachable.date() + timedelta(days=1):
            return f"gives up more nights than the delay requires"
        return True

    def rank(self, item, task):
        # Nights the member paid for and cannot use are the expensive mistake
        # here, so forfeiture dominates unless the task says otherwise.
        forfeited = item.meta.get("forfeited", 0.0)
        if task.constraints.get("priority") == "cost":
            return (item.cost_delta, forfeited)
        return (forfeited, item.cost_delta, -item.quality)


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------

class ActivityRecoveryAgent(RecoveryAgent):
    """Dated-entry inventory. The distinctive rule is that a passport cannot be
    used before the member is in the country, and re-dating has to land on a day
    that is still inside the trip."""

    name = "Activity Agent"
    connector = "activities"
    handles = (BookingKind.ACTIVITY,)

    def feasible(self, item, task, itinerary):
        base = super().feasible(item, task, itinerary)
        if base is not True:
            return base

        arrival = task.constraints.get("arrival")
        if arrival:
            arrival_at = datetime.fromisoformat(arrival)
            buffer = timedelta(minutes=task.constraints.get("arrival_buffer_minutes", 720))
            if item.start != item.end and item.start < arrival_at + buffer:
                return f"entry {item.start:%d %b %H:%M} is before the member lands plus the {buffer} buffer"

        must_end_before = task.constraints.get("must_end_before")
        if must_end_before and item.start != item.end and item.end > datetime.fromisoformat(must_end_before):
            return f"runs past {datetime.fromisoformat(must_end_before):%d %b %H:%M}, when the onward flight closes"
        return True

    def rank(self, item, task):
        # Deterministic fallback for the model-backed activity assessment.
        return (-item.quality, item.cost_delta)


# ---------------------------------------------------------------------------
# Dining
# ---------------------------------------------------------------------------

class DiningRecoveryAgent(RecoveryAgent):
    name = "Dining Agent"
    connector = "dining"
    handles = (BookingKind.DINING,)

    def feasible(self, item, task, itinerary):
        base = super().feasible(item, task, itinerary)
        if base is not True:
            return base

        arrival = task.constraints.get("arrival")
        if arrival and item.start != item.end:
            reachable = datetime.fromisoformat(arrival) + timedelta(
                minutes=task.constraints.get("arrival_buffer_minutes", 150)
            )
            if item.start < reachable:
                return f"seating {item.start:%d %b %H:%M} is before the member could get to Shinjuku"

        trip_end = task.constraints.get("in_city_until")
        if trip_end and item.start != item.end and item.start > datetime.fromisoformat(trip_end):
            return "falls after the member has left Tokyo"
        return True

    def rank(self, item, task):
        return (item.cost_delta, -item.quality)


# ---------------------------------------------------------------------------
# Ground transfer
# ---------------------------------------------------------------------------

class GroundRecoveryAgent(RecoveryAgent):
    name = "Ground Agent"
    connector = "ground"
    handles = (BookingKind.GROUND,)

    def feasible(self, item, task, itinerary):
        base = super().feasible(item, task, itinerary)
        if base is not True:
            return base

        arrival = task.constraints.get("arrival")
        if arrival and item.start != item.end:
            reachable = datetime.fromisoformat(arrival) + timedelta(
                minutes=task.constraints.get("arrival_buffer_minutes", 60)
            )
            if item.start < reachable:
                return f"departs {item.start:%d %b %H:%M}, before immigration and baggage are cleared"
            if item.start > reachable + timedelta(hours=6):
                return "leaves the member waiting at the airport for hours"
        return True

    def rank(self, item, task):
        return (item.cost_delta, -item.quality)


AGENTS: Dict[str, RecoveryAgent] = {
    "flights": FlightRecoveryAgent(),
    "lodging": AccommodationRecoveryAgent(),
    "activities": ActivityRecoveryAgent(),
    "dining": DiningRecoveryAgent(),
    "ground": GroundRecoveryAgent(),
}


def agent_for(kind: BookingKind) -> RecoveryAgent:
    return AGENTS[connectors.connector_for(kind)]


def agent_roster() -> List[Dict[str, object]]:
    return [
        {
            "name": agent.name,
            "connector": key,
            "adapter": connectors.SPECS[key].adapter,
            # One-release compatibility alias for the original UI payload.
            "server": connectors.SPECS[key].server,
            "handles": [kind.value for kind in agent.handles],
            "tools": list(connectors.SPECS[key].tools),
        }
        for key, agent in AGENTS.items()
    ]
