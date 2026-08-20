"""TripShield — the Travel Recovery Orchestrator.

Layering, top to bottom:

    web/                  human-in-the-loop planning and approval interface
    api/index.py          HTTP surface
    orchestrator.py       workflow: detect -> reconstruct -> plan -> approve -> execute
    agents.py             specialized recovery subagents
    connectors.py         MCP-style clients onto real travel APIs
    graph.py              dependency graph and impact propagation
    optimizer.py          multi-objective ranking of candidate plans
    execution.py          transactional execution and compensation
    catalog.py            the demonstration booking history
    domain.py             types shared by all of the above

Every price, seat and reference in ``catalog.py`` is synthetic. The *shapes* —
endpoint paths, status vocabularies, two-phase cancellation quotes — are taken
from the real APIs named in ``connectors.py``.
"""

__all__ = ["domain", "catalog", "graph", "connectors", "agents", "optimizer", "orchestrator", "execution", "store"]
