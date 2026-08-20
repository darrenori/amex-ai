"""Session state.

Deliberately in-process. Everything the orchestrator needs is derivable from the
seed catalog, so a session is cheap to rebuild and is rebuilt on demand when a
lookup misses.

Execution runs are the exception — they record transactions that actually
happened, so they cannot be reconstructed from seed data. If a run is missing,
the API says so rather than inventing one. On a single local process that never
happens; on a serverless deployment with more than one warm instance it can, and
the honest 410 is better than a silently different run.

A production system would put runs in Postgres or Durable Objects and key
compensation off the transaction log. The interface here is the same shape, so
that swap touches this file and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import catalog
from .domain import ExecutionRun, Option, RecoveryPlan
from .graph import Itinerary


@dataclass
class Session:
    id: str
    itinerary: Itinerary
    cancelled: List[str] = field(default_factory=list)
    catalogue: Dict[str, Option] = field(default_factory=dict)   # option_id -> Option
    plans: Dict[str, RecoveryPlan] = field(default_factory=dict)
    runs: Dict[str, ExecutionRun] = field(default_factory=dict)
    last_planning: Optional[dict] = None
    plan_version: int = 1


_SESSIONS: Dict[str, Session] = {}


def fresh_itinerary() -> Itinerary:
    return Itinerary(catalog.build_bookings(), catalog.build_dependencies())


def get(session_id: str) -> Session:
    """Existing session, or a newly seeded one. Never raises."""
    session = _SESSIONS.get(session_id)
    if session is None:
        session = Session(id=session_id, itinerary=fresh_itinerary())
        _SESSIONS[session_id] = session
    return session


def reset(session_id: str) -> Session:
    _SESSIONS[session_id] = Session(id=session_id, itinerary=fresh_itinerary())
    return _SESSIONS[session_id]


def run(session_id: str, run_id: str) -> Optional[ExecutionRun]:
    return get(session_id).runs.get(run_id)
