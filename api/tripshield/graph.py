"""The dependency graph, and impact propagation over it.

The point of this module is that most of the reasoning about *what broke* needs
no model at all. Whether a member can still reach a timed park entry is
arithmetic:

    if new_arrival + minimum_buffer > event.start:  the event is unreachable

What a model is useful for sits one layer up — wording the trade-off, deciding
which of several defensible plans to lead with, handling the genuinely ambiguous
"could they still make dinner if they skipped the bag drop" cases. Those live in
``agents.py``. This file stays deterministic and testable.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .domain import (
    Booking,
    BookingStatus,
    Dependency,
    Flexibility,
    Severity,
    Verdict,
)


class Itinerary:
    """Bookings plus the edges between them, with the traversal helpers the
    orchestrator needs."""

    def __init__(self, bookings: Dict[str, Booking], dependencies: List[Dependency]):
        self.bookings = bookings
        self.dependencies = [d for d in dependencies if d.source in bookings and d.target in bookings]

        self.outgoing: Dict[str, List[Dependency]] = defaultdict(list)
        self.incoming: Dict[str, List[Dependency]] = defaultdict(list)
        for edge in self.dependencies:
            self.outgoing[edge.source].append(edge)
            self.incoming[edge.target].append(edge)

    # -- traversal ---------------------------------------------------------

    def topological_order(self) -> List[str]:
        """Kahn's algorithm. Ties break on start time so the order also reads
        chronologically in the UI."""
        indegree = {bid: len(self.incoming[bid]) for bid in self.bookings}
        ready = deque(sorted(
            (bid for bid, n in indegree.items() if n == 0),
            key=lambda bid: self.bookings[bid].start,
        ))
        order: List[str] = []

        while ready:
            current = ready.popleft()
            order.append(current)
            newly_ready = []
            for edge in self.outgoing[current]:
                indegree[edge.target] -= 1
                if indegree[edge.target] == 0:
                    newly_ready.append(edge.target)
            for bid in sorted(newly_ready, key=lambda b: self.bookings[b].start):
                ready.append(bid)

        if len(order) != len(self.bookings):
            # A cycle would be a modelling bug; fall back to chronological so the
            # demo degrades into something readable rather than raising.
            return sorted(self.bookings, key=lambda bid: self.bookings[bid].start)
        return order

    def descendants(self, booking_id: str) -> Set[str]:
        seen: Set[str] = set()
        queue = deque(edge.target for edge in self.outgoing[booking_id])
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            queue.extend(edge.target for edge in self.outgoing[node])
        return seen

    # -- structural edits --------------------------------------------------

    def splice_out(self, node_ids: Iterable[str]) -> "Itinerary":
        """Remove bookings the member has chosen to give up, reconnecting around them.

        Dropping a booking is not the same as breaking it. If the member cancels
        the Narita Express, they have not stopped needing to reach the hotel —
        so ``flight → transfer → hotel`` becomes ``flight → hotel`` with the two
        buffers added, rather than the hotel inheriting a broken dependency and
        cascading. The spliced edge is HARD only when both originals were: a
        chain is no stronger than its weakest link.
        """
        drop = {nid for nid in node_ids if nid in self.bookings}
        if not drop:
            return self

        bookings = {bid: b for bid, b in self.bookings.items() if bid not in drop}
        edges = [e for e in self.dependencies if e.source not in drop and e.target not in drop]

        for node in drop:
            for before in self.incoming[node]:
                if before.source in drop:
                    continue
                for after in self.outgoing[node]:
                    if after.target in drop:
                        continue
                    edges.append(Dependency(
                        source=before.source,
                        target=after.target,
                        type=before.type,
                        severity=(
                            Severity.HARD
                            if before.severity is Severity.HARD and after.severity is Severity.HARD
                            else Severity.SOFT
                        ),
                        min_buffer=before.min_buffer + after.min_buffer,
                        rationale=(
                            f"{self.bookings[node].label} was given up; the journey from "
                            f"{self.bookings[before.source].label} still has to be made."
                        ),
                    ))

        return Itinerary(bookings, edges)

    def public(self) -> Dict:
        return {
            "nodes": [self.bookings[bid].public() for bid in self.topological_order()],
            "edges": [edge.public() for edge in self.dependencies],
        }


# ---------------------------------------------------------------------------
# Impact propagation
# ---------------------------------------------------------------------------

def _latest_start(booking: Booking, proposed_start: Optional[datetime] = None) -> datetime:
    """The last moment this booking can begin and still be usable.

    When an amendment moves the booking, the supplier's own cut-off moves with
    it by the same amount — a stay re-dated to the 19th has the 19th's desk
    cut-off, not the 18th's. What does *not* happen is the cut-off stretching
    just because the member is arriving late on the original date.
    """
    raw = booking.meta.get("latest_start") or booking.meta.get("latest_check_in")
    deadline = datetime.fromisoformat(raw) if raw else booking.start
    if proposed_start is not None:
        deadline += proposed_start - booking.start
    return deadline


def _exposure(booking: Booking) -> float:
    """Money that does not come back if this booking is simply abandoned."""
    return max(booking.amount - booking.refundable, 0.0)


def propagate(
    itinerary: Itinerary,
    *,
    cancelled: Iterable[str] = (),
    replacements: Optional[Dict[str, Tuple[datetime, datetime]]] = None,
) -> Dict[str, Verdict]:
    """Walk the graph in dependency order and decide the fate of every node.

    ``cancelled``    bookings the supplier has withdrawn — they produce no arrival.
    ``replacements`` bookings whose times have been changed by a recovery option.

    Returns one :class:`Verdict` per booking. The caller does not have to
    pre-compute which nodes are downstream; nodes with no violated incoming edge
    come back ``UNAFFECTED``.
    """
    cancelled_set = set(cancelled)
    replacements = replacements or {}

    verdicts: Dict[str, Verdict] = {}
    # Effective timing after everything upstream has been applied. ``None`` end
    # means the node never happens, so it can never satisfy a downstream edge.
    effective: Dict[str, Optional[Tuple[datetime, datetime]]] = {}

    for booking_id in itinerary.topological_order():
        booking = itinerary.bookings[booking_id]

        if booking_id in cancelled_set:
            effective[booking_id] = None
            verdicts[booking_id] = Verdict(
                booking_id=booking_id,
                status=BookingStatus.CANCELLED,
                reason="Withdrawn by the supplier.",
                exposure=_exposure(booking),
            )
            continue

        # A replacement proposes new times; it does not exempt the booking from
        # its own dependencies. A hotel amendment that claims a 19 September
        # check-in is still wrong if the member does not land until the 20th, so
        # replaced nodes fall through to exactly the same edge checks below.
        replaced = booking_id in replacements
        start, end = replacements[booking_id] if replaced else (booking.start, booking.end)
        proposed = start if replaced else None
        worst: Optional[Verdict] = None

        for edge in itinerary.incoming[booking_id]:
            upstream = effective.get(edge.source)

            # The upstream node does not happen at all.
            if upstream is None:
                if edge.severity is Severity.HARD:
                    candidate = Verdict(
                        booking_id=booking_id,
                        status=BookingStatus.BROKEN,
                        reason=(
                            f"{itinerary.bookings[edge.source].label} no longer happens, and this "
                            f"booking depends on it ({edge.rationale.rstrip('.')})."
                        ),
                        slack_minutes=None,
                        violated_edge=edge,
                        exposure=_exposure(booking),
                    )
                else:
                    candidate = Verdict(
                        booking_id=booking_id,
                        status=BookingStatus.AT_RISK,
                        reason=f"{itinerary.bookings[edge.source].label} no longer happens; this becomes uncertain.",
                        violated_edge=edge,
                        exposure=_exposure(booking),
                    )
                worst = _worse(worst, candidate)
                continue

            # When the member becomes free to move on: a stay releases them at
            # check-in, everything else at its end.
            source = itinerary.bookings[edge.source]
            free_at = upstream[0] if source.is_container else upstream[1]
            earliest = free_at + edge.min_buffer

            if booking.flexibility is Flexibility.SHIFTABLE:
                deadline = _latest_start(booking, proposed)
                slack = int((deadline - earliest).total_seconds() // 60)
                if earliest > deadline:
                    candidate = Verdict(
                        booking_id=booking_id,
                        status=BookingStatus.BROKEN if edge.severity is Severity.HARD else BookingStatus.AT_RISK,
                        reason=(
                            f"Earliest possible arrival is {earliest:%d %b %H:%M}, past the "
                            f"{deadline:%H:%M} cut-off."
                        ),
                        slack_minutes=slack,
                        violated_edge=edge,
                        exposure=_exposure(booking),
                    )
                    worst = _worse(worst, candidate)
                elif earliest > start:
                    # It still works, it just starts later. That shift propagates.
                    start = earliest
                    worst = _worse(worst, Verdict(
                        booking_id=booking_id,
                        status=BookingStatus.AT_RISK,
                        reason=f"Shifted later — now begins {earliest:%d %b %H:%M}, within the cut-off.",
                        slack_minutes=slack,
                        violated_edge=edge,
                    ))
                continue

            # FIXED and REBOOKABLE both have to be met at their own start time.
            slack = int((start - earliest).total_seconds() // 60)
            if earliest > start:
                if edge.severity is Severity.HARD:
                    candidate = Verdict(
                        booking_id=booking_id,
                        status=BookingStatus.BROKEN,
                        reason=(
                            f"Needs to begin {start:%d %b %H:%M} but the earliest reachable time is "
                            f"{earliest:%d %b %H:%M} — {abs(slack)} minutes short."
                        ),
                        slack_minutes=slack,
                        violated_edge=edge,
                        exposure=_exposure(booking),
                    )
                else:
                    candidate = Verdict(
                        booking_id=booking_id,
                        status=BookingStatus.AT_RISK,
                        reason=(
                            f"{abs(slack)} minutes short of the {edge.min_buffer.seconds // 60}-minute "
                            f"buffer — reachable only if nothing else slips."
                        ),
                        slack_minutes=slack,
                        violated_edge=edge,
                        exposure=_exposure(booking),
                    )
                worst = _worse(worst, candidate)

        # A container's end is fixed: checking in at midnight does not push
        # checkout to midnight three days later. Everything else carries its
        # duration with it when it slips.
        if replaced:
            settled_end = end
        elif booking.is_container:
            settled_end = booking.end
        else:
            settled_end = start + (booking.end - booking.start)
        broken = bool(worst and worst.status is BookingStatus.BROKEN)
        effective[booking_id] = None if broken else (start, settled_end)

        verdicts[booking_id] = worst or Verdict(
            booking_id=booking_id,
            status=BookingStatus.REBOOKED if replaced else BookingStatus.UNAFFECTED,
            reason=(
                "Replaced by the selected recovery option, and every dependency still holds."
                if replaced else "Every incoming dependency still holds."
            ),
            slack_minutes=_min_slack(itinerary, booking_id, effective, proposed),
        )

    return verdicts


_RANK = {
    BookingStatus.UNAFFECTED: 0,
    BookingStatus.CONFIRMED: 0,
    BookingStatus.REBOOKED: 1,
    BookingStatus.AT_RISK: 2,
    BookingStatus.BROKEN: 3,
    BookingStatus.CANCELLED: 4,
}


def _worse(current: Optional[Verdict], candidate: Verdict) -> Verdict:
    """Keep the most severe verdict a node collects across its incoming edges."""
    if current is None:
        return candidate
    return candidate if _RANK[candidate.status] > _RANK[current.status] else current


def _min_slack(
    itinerary: Itinerary,
    booking_id: str,
    effective: Dict[str, Optional[Tuple[datetime, datetime]]],
    proposed: Optional[datetime] = None,
) -> Optional[int]:
    """Tightest remaining margin on an unaffected node — the number that tells a
    reviewer how close this one came."""
    margins: List[int] = []
    booking = itinerary.bookings[booking_id]
    for edge in itinerary.incoming[booking_id]:
        upstream = effective.get(edge.source)
        if upstream is None:
            continue
        source = itinerary.bookings[edge.source]
        earliest = (upstream[0] if source.is_container else upstream[1]) + edge.min_buffer
        if booking.flexibility is Flexibility.SHIFTABLE:
            reference = _latest_start(booking, proposed)
        else:
            reference = proposed or booking.start
        margins.append(int((reference - earliest).total_seconds() // 60))
    return min(margins) if margins else None


def affected(verdicts: Dict[str, Verdict]) -> List[str]:
    """Bookings that need a recovery task, most severe first."""
    rows = [
        (bid, v) for bid, v in verdicts.items()
        if v.status in (BookingStatus.CANCELLED, BookingStatus.BROKEN, BookingStatus.AT_RISK)
    ]
    rows.sort(key=lambda row: (-_RANK[row[1].status], row[0]))
    return [bid for bid, _ in rows]


def total_exposure(verdicts: Dict[str, Verdict]) -> float:
    return sum(v.exposure for v in verdicts.values())
