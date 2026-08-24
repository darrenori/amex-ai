"""Offline contract tests for the optional MCP/model explanation seam."""

import asyncio
import copy
import json
from types import SimpleNamespace

from api.tripshield import ai
from api.tripshield.mcp_server import MCPClient, TOOL_NAMES, create_mcp_server


GRAPH = {"nodes": [{"id": "bk_flight"}], "edges": []}
PLANS = [
    {
        "id": "plan_01",
        "selections": {"bk_flight": "opt_flight_01"},
        "metrics": {"cost_delta": 100, "hours_lost": 2},
        "valid": True,
    },
    {
        "id": "plan_02",
        "selections": {"bk_flight": "opt_flight_02"},
        "metrics": {"cost_delta": 30, "hours_lost": 8},
        "valid": True,
    },
]
RANKING = {
    "recommended_plan_id": "plan_01",
    "order": ["plan_01", "plan_02"],
    "reason_codes": {"plan_01": ["LOW_DELAY"]},
}
HISTORY = {"id": "time", "history": [{"text": "Paid more to save five hours."}]}


def _model_output(recommended="plan_01"):
    return {
        "recommended_plan_id": recommended,
        "ranking_rationale": "Plan 01 preserves time at a modest premium.",
        "member_explanation": "This option gets you there sooner with fewer changes.",
        "contextual_judgements": {
            "flight": "The direct replacement limits delay.",
            "activity": "The planned activity remains reachable.",
        },
        "referenced_plan_ids": ["plan_01", "plan_02"],
        "referenced_option_ids": ["opt_flight_01", "opt_flight_02"],
    }


class FakeMCPClient:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def list_tools(self):
        return SimpleNamespace(tools=[
            SimpleNamespace(
                name=name,
                description="read-only",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            )
            for name in TOOL_NAMES
        ])

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            is_error=False,
            structured_content={"tool": name},
            content=[],
        )


class FakeResponses:
    def __init__(self, output, duplicate=False):
        self.output = output
        self.duplicate = duplicate
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            names = list(TOOL_NAMES)
            if self.duplicate:
                names = [TOOL_NAMES[0], TOOL_NAMES[0]]
            calls = [
                SimpleNamespace(
                    type="function_call",
                    name=name,
                    arguments="{}",
                    call_id=f"call_{index}",
                )
                for index, name in enumerate(names)
            ]
            return SimpleNamespace(id="resp_1", output=calls, output_text="")
        return SimpleNamespace(
            id="resp_2",
            output=[],
            output_text=json.dumps(self.output),
        )


class FakeOpenAI:
    def __init__(self, output, duplicate=False):
        self.responses = FakeResponses(output, duplicate=duplicate)


class FakeMessages:
    def __init__(self, output):
        self.output = output
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            return SimpleNamespace(content=[
                SimpleNamespace(type="tool_use", name=name, input={}, id=f"tool_{index}")
                for index, name in enumerate(TOOL_NAMES)
            ])
        return SimpleNamespace(content=[
            SimpleNamespace(type="text", text=json.dumps(self.output))
        ])


class FakeAnthropic:
    def __init__(self, output):
        self.messages = FakeMessages(output)


def _run_with(provider, provider_client, mcp):
    environment = {
        "AI_PROVIDER": provider,
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        "OPENAI_API_KEY": "test-openai-key",
    }
    return asyncio.run(ai.generate_ai_insight(
        graph=GRAPH,
        plans=PLANS,
        ranking=RANKING,
        member_history=HISTORY,
        provider_client=provider_client,
        mcp_client_factory=lambda _server: mcp,
        env=environment,
    ))


def test_request_snapshot_is_isolated_and_exposes_exactly_three_read_tools():
    graph = copy.deepcopy(GRAPH)
    plans = copy.deepcopy(PLANS)
    bound = create_mcp_server(
        graph=graph,
        plans=plans,
        ranking=RANKING,
        member_history=HISTORY,
    )

    graph["nodes"][0]["id"] = "mutated_source"
    first = bound.call_local_tool("get_trip_graph")
    first["nodes"][0]["id"] = "mutated_result"
    second = bound.call_local_tool("get_trip_graph")

    assert second["nodes"][0]["id"] == "bk_flight"
    assert tuple(tool.name for tool in bound.tool_schemas) == TOOL_NAMES
    assert all(tool.input_schema["additionalProperties"] is False for tool in bound.tool_schemas)


def test_real_mcp_v2_client_discovers_and_calls_the_bound_server_when_installed():
    bound = create_mcp_server(
        graph=GRAPH,
        plans=PLANS,
        ranking=RANKING,
        member_history=HISTORY,
    )
    if not bound.sdk_available:
        return

    async def exercise():
        async with MCPClient(bound.server) as client:
            listed = await client.list_tools()
            names = [tool.name for tool in listed.tools]
            results = [await client.call_tool(name, {}) for name in names]
            return names, results

    names, results = asyncio.run(exercise())
    assert names == list(TOOL_NAMES)
    assert all(not result.is_error for result in results)


def test_openai_discovers_and_calls_all_mcp_tools_once_then_validates_output():
    mcp = FakeMCPClient()
    provider = FakeOpenAI(_model_output())

    result = _run_with("openai", provider, mcp)

    assert result["status"] == "generated"
    assert result["recommended_plan_id"] == RANKING["recommended_plan_id"]
    assert result["tools_used"] == list(TOOL_NAMES)
    assert [name for name, _ in mcp.calls] == list(TOOL_NAMES)
    assert [tool["name"] for tool in provider.responses.requests[0]["tools"]] == list(TOOL_NAMES)
    assert provider.responses.requests[0]["reasoning"] == {"effort": "low"}
    assert provider.responses.requests[-1]["text"]["format"]["strict"] is True


def test_anthropic_uses_the_same_discovered_mcp_tools():
    mcp = FakeMCPClient()
    provider = FakeAnthropic(_model_output())

    result = _run_with("anthropic", provider, mcp)

    assert result["status"] == "generated"
    assert result["provider"] == "anthropic"
    assert result["tools_used"] == list(TOOL_NAMES)
    assert [tool["name"] for tool in provider.messages.requests[0]["tools"]] == list(TOOL_NAMES)
    assert "output_config" in provider.messages.requests[-1]


def test_repeated_tool_call_fails_closed_without_returning_partial_prose():
    result = _run_with(
        "openai",
        FakeOpenAI(_model_output(), duplicate=True),
        FakeMCPClient(),
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "repeated_tool"
    assert result["tools_used"] == []
    assert result["member_explanation"] is None


def test_model_cannot_replace_the_deterministic_recommendation():
    result = _run_with(
        "openai",
        FakeOpenAI(_model_output(recommended="plan_02")),
        FakeMCPClient(),
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "recommendation_mismatch"
    assert result["recommended_plan_id"] == RANKING["recommended_plan_id"]


def test_explicit_provider_with_missing_key_does_not_fall_back():
    result = asyncio.run(ai.generate_ai_insight(
        graph=GRAPH,
        plans=PLANS,
        ranking=RANKING,
        member_history=HISTORY,
        env={"AI_PROVIDER": "anthropic", "OPENAI_API_KEY": "present"},
    ))

    assert result["status"] == "disabled"
    assert result["provider"] == "anthropic"
    assert result["error_code"] == "missing_api_key"


def test_implicit_selection_prefers_anthropic_and_status_is_import_safe():
    status = ai.ai_status({
        "ANTHROPIC_API_KEY": "present",
        "OPENAI_API_KEY": "also-present",
    })

    assert status["provider"] == "anthropic"
    assert status["model"] == ai.DEFAULT_ANTHROPIC_MODEL
    assert status["tools"] == list(TOOL_NAMES)
    assert status["status"] in {"available", "unavailable"}


def test_lowercase_deployment_key_selects_openai_with_the_api_model_default():
    selected = ai._selection({"openai_api_key": "vercel-secret"})

    assert selected["provider"] == "openai"
    assert selected["api_key"] == "vercel-secret"
    assert selected["model"] == "gpt-5.6"
