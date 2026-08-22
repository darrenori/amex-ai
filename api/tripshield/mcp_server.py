"""Request-bound, read-only MCP context for TripShield's explanation models.

The model is intentionally given three narrow views over an immutable planning
snapshot.  There are no write tools and no process-global request state.  When
the MCP v2 SDK is installed, :class:`mcp.Client` connects directly to the
returned ``MCPServer`` object in memory; importing this module remains safe in
offline/test environments where the optional SDK is absent.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:  # MCP is optional so deterministic planning never depends on this import.
    from mcp import Client as MCPClient  # type: ignore
    from mcp.server import MCPServer  # type: ignore
except (ImportError, ModuleNotFoundError):  # pragma: no cover - environment dependent
    MCPClient = None  # type: ignore
    MCPServer = None  # type: ignore


TOOL_NAMES: Tuple[str, ...] = (
    "get_trip_graph",
    "list_candidate_plans",
    "get_member_choice_history",
)

_TOOL_DESCRIPTIONS = {
    "get_trip_graph": (
        "Read the immutable trip graph, including bookings, dependencies, "
        "the disruption, and deterministic impact assessment."
    ),
    "list_candidate_plans": (
        "Read the immutable deterministic candidate plans, metrics, scores, "
        "reason codes, option provenance, and recommended plan id."
    ),
    "get_member_choice_history": (
        "Read the immutable synthetic member preference profile and the "
        "past choices used by the deterministic ranker."
    ),
}


def _frozen_copy(value: Any) -> Any:
    """Recursively copy into immutable containers for closure-safe snapshots."""

    if isinstance(value, Mapping):
        return _FrozenMap(tuple(
            (str(key), _frozen_copy(item)) for key, item in value.items()
        ))
    if isinstance(value, (list, tuple)):
        return _FrozenSequence(tuple(_frozen_copy(item) for item in value))
    if isinstance(value, set):
        return _FrozenSequence(tuple(sorted(
            (_frozen_copy(item) for item in value), key=repr
        )))
    return copy.deepcopy(value)


def _thaw(value: Any) -> Any:
    """Return a fresh JSON-like copy from an immutable snapshot."""

    if isinstance(value, _FrozenMap):
        return {key: _thaw(item) for key, item in value.items}
    if isinstance(value, _FrozenSequence):
        return [_thaw(item) for item in value.items]
    return copy.deepcopy(value)


@dataclass(frozen=True)
class MCPToolSchema:
    name: str
    description: str
    input_schema: Dict[str, Any]


@dataclass(frozen=True)
class _FrozenMap:
    items: Tuple[Tuple[str, Any], ...]


@dataclass(frozen=True)
class _FrozenSequence:
    items: Tuple[Any, ...]


class RequestBoundMCP:
    """One immutable planning snapshot and, when installed, its MCP server."""

    def __init__(
        self,
        *,
        graph: Dict[str, Any],
        plans: List[Dict[str, Any]],
        ranking: Dict[str, Any],
        member_history: Dict[str, Any],
    ) -> None:
        self._graph = _frozen_copy(graph)
        self._plans = _frozen_copy({"plans": plans, "ranking": ranking})
        self._member_history = _frozen_copy(member_history)
        self.server: Optional[Any] = self._build_sdk_server()

    @property
    def sdk_available(self) -> bool:
        return self.server is not None and MCPClient is not None

    @property
    def tool_schemas(self) -> Tuple[MCPToolSchema, ...]:
        """Static fallback metadata, also useful for dependency-free tests."""

        empty = {"type": "object", "properties": {}, "additionalProperties": False}
        return tuple(
            MCPToolSchema(name, _TOOL_DESCRIPTIONS[name], copy.deepcopy(empty))
            for name in TOOL_NAMES
        )

    def call_local_tool(self, name: str) -> Any:
        """Execute the same read-only closure used by MCP.

        Provider adapters do not call this compatibility helper; they always
        discover and dispatch via an MCP client.  It exists so immutability can
        be verified even when the optional SDK is not installed.
        """

        if name == "get_trip_graph":
            return _thaw(self._graph)
        if name == "list_candidate_plans":
            return _thaw(self._plans)
        if name == "get_member_choice_history":
            return _thaw(self._member_history)
        raise KeyError("Unknown TripShield MCP tool")

    def _build_sdk_server(self) -> Optional[Any]:
        if MCPServer is None:
            return None

        server = MCPServer(
            "TripShield",
            instructions=(
                "Read all three tools exactly once. Explain the deterministic "
                "recommendation; never change scores, ordering, or feasibility."
            ),
        )

        @server.tool(title="Get trip graph")
        def get_trip_graph() -> dict:
            """Read bookings, dependencies, disruption, and impact."""

            return self.call_local_tool("get_trip_graph")

        @server.tool(title="List candidate plans")
        def list_candidate_plans() -> dict:
            """Read deterministic plans, ranking, metrics, and provenance."""

            return self.call_local_tool("list_candidate_plans")

        @server.tool(title="Get member choice history")
        def get_member_choice_history() -> dict:
            """Read the selected synthetic preference profile and history."""

            return self.call_local_tool("get_member_choice_history")

        return server


def create_mcp_server(
    *,
    graph: Dict[str, Any],
    plans: List[Dict[str, Any]],
    ranking: Dict[str, Any],
    member_history: Dict[str, Any],
) -> RequestBoundMCP:
    """Create an isolated read-only server for exactly one planning request."""

    return RequestBoundMCP(
        graph=graph,
        plans=plans,
        ranking=ranking,
        member_history=member_history,
    )


def mcp_sdk_available() -> bool:
    return MCPClient is not None and MCPServer is not None
