"""Fail-closed structured model calls for TripShield's bounded AI agents.

Models read immutable, role-scoped planning snapshots through MCP and return
strict structured assessments or a personalized order. They never receive an
interface capable of changing supplier facts, plan scores, validity, reason
codes, approval, or execution. The optimizer validates any returned order.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .mcp_server import MCPClient, TOOL_NAMES, create_mcp_server, mcp_sdk_available

try:  # Optional: deterministic planning must import without provider packages.
    import anthropic as _anthropic  # type: ignore
except (ImportError, ModuleNotFoundError):  # pragma: no cover - environment dependent
    _anthropic = None

try:
    import openai as _openai  # type: ignore
except (ImportError, ModuleNotFoundError):  # pragma: no cover - environment dependent
    _openai = None


DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_OPENAI_MODEL = "gpt-5.6-sol"
DEFAULT_TIMEOUT_SECONDS = 8.0
MAX_MODEL_ROUNDS = 5

# Strict structured output rejects `uniqueItems` (OpenAI returns 400
# invalid_json_schema before it even reads the request), so uniqueness of the
# referenced-id arrays is asserted in `_validated_output` instead of declared.
AI_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "recommended_plan_id": {"type": "string"},
        "ranking_rationale": {"type": "string", "minLength": 1, "maxLength": 4000},
        "member_explanation": {"type": "string", "minLength": 1, "maxLength": 2000},
        "contextual_judgements": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "flight": {"type": "string", "maxLength": 1500},
                "activity": {"type": "string", "maxLength": 1500},
            },
            "required": ["flight", "activity"],
        },
        "referenced_plan_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "referenced_option_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "recommended_plan_id",
        "ranking_rationale",
        "member_explanation",
        "contextual_judgements",
        "referenced_plan_ids",
        "referenced_option_ids",
    ],
}

_SYSTEM_PROMPT = """You explain a deterministic travel-recovery recommendation.
Call each available TripShield tool exactly once before answering. Treat plan
ordering, scores, feasibility, Pareto flags, reason codes, and the recommended
plan id as immutable facts. Explain the winner and relevant flight/activity
trade-offs concisely. Never propose a different winner. Return only the
declared structured JSON object and list every plan/option id you reference.
"""


class _AIFlowError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _env(source: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return source if source is not None else os.environ


def _selection(source: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    environ = _env(source)
    explicit = str(environ.get("AI_PROVIDER", "")).strip().lower()
    if explicit and explicit not in {"anthropic", "openai"}:
        return {
            "provider": None,
            "model": None,
            "credential_present": False,
            "error_code": "invalid_provider",
            "explicit": True,
        }

    anthropic_key = str(environ.get("ANTHROPIC_API_KEY", "")).strip()
    openai_key = str(environ.get("OPENAI_API_KEY", "")).strip()
    if explicit:
        provider = explicit
    elif anthropic_key:
        provider = "anthropic"
    elif openai_key:
        provider = "openai"
    else:
        provider = None

    if provider == "anthropic":
        return {
            "provider": provider,
            "model": str(environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)),
            "api_key": anthropic_key,
            "credential_present": bool(anthropic_key),
            "error_code": None if anthropic_key else "missing_api_key",
            "explicit": bool(explicit),
        }
    if provider == "openai":
        return {
            "provider": provider,
            "model": str(environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)),
            "api_key": openai_key,
            "credential_present": bool(openai_key),
            "error_code": None if openai_key else "missing_api_key",
            "explicit": bool(explicit),
        }
    return {
        "provider": None,
        "model": None,
        "credential_present": False,
        "error_code": "missing_api_key",
        "explicit": False,
    }


def ai_status(env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Return configuration/package availability without contacting a provider."""

    selected = _selection(env)
    provider = selected.get("provider")
    package_available = (
        _anthropic is not None if provider == "anthropic"
        else _openai is not None if provider == "openai"
        else False
    )
    error_code = selected.get("error_code")
    if not error_code and not mcp_sdk_available():
        error_code = "mcp_sdk_unavailable"
    if not error_code and not package_available:
        error_code = "provider_sdk_unavailable"
    return {
        "status": "available" if not error_code else "unavailable",
        "workflow": "bounded_multi_agent",
        "agent_roles": [
            "flight", "accommodation", "activity", "dining", "ground", "recommendation"
        ],
        "provider": provider,
        "model": selected.get("model"),
        "credential_present": selected.get("credential_present", False),
        "transport": "in_process" if mcp_sdk_available() else "unavailable",
        "tools": list(TOOL_NAMES),
        "error_code": error_code,
    }


def _result_base(provider: Optional[str], model: Optional[str]) -> Dict[str, Any]:
    return {
        "status": "failed",
        "provider": provider,
        "model": model,
        "transport": "in_process",
        "tools_used": [],
        "recommended_plan_id": None,
        "ranking_rationale": None,
        "member_explanation": None,
        "contextual_judgements": {"flight": "", "activity": ""},
        "latency_ms": 0,
        "error_code": None,
    }


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


@asynccontextmanager
async def _mcp_context(bound: Any, client_factory: Optional[Any]):
    if client_factory is None:
        if not bound.sdk_available or MCPClient is None:
            raise _AIFlowError("mcp_sdk_unavailable")
        client = MCPClient(bound.server)
    else:
        client = client_factory(bound.server)
        client = await _maybe_await(client)
    async with client as connected:
        yield connected


async def _discover_tools(
    client: Any,
    expected_names: Sequence[str] = TOOL_NAMES,
) -> List[Dict[str, Any]]:
    result = await _maybe_await(client.list_tools())
    raw_tools = _value(result, "tools", [])
    tools: List[Dict[str, Any]] = []
    for raw in raw_tools:
        tools.append({
            "name": _value(raw, "name"),
            "description": _value(raw, "description", ""),
            "input_schema": _value(
                raw, "input_schema", _value(raw, "inputSchema", {"type": "object"})
            ),
        })
    if tuple(tool["name"] for tool in tools) != tuple(expected_names):
        raise _AIFlowError("invalid_mcp_tools")
    return tools


async def _call_mcp_tool(client: Any, name: str, arguments: Any) -> Any:
    if not isinstance(arguments, Mapping):
        raise _AIFlowError("invalid_tool_arguments")
    result = await _maybe_await(client.call_tool(name, dict(arguments)))
    if bool(_value(result, "is_error", False)):
        raise _AIFlowError("mcp_tool_error")
    structured = _value(result, "structured_content")
    if structured is not None:
        return structured
    for block in _value(result, "content", []) or []:
        text = _value(block, "text")
        if text:
            try:
                return json.loads(text)
            except (TypeError, ValueError):
                return text
    raise _AIFlowError("empty_mcp_result")


def _record_tool(
    name: str,
    used: List[str],
    expected_names: Sequence[str] = TOOL_NAMES,
) -> None:
    if name not in expected_names:
        raise _AIFlowError("unknown_tool")
    if name in used:
        raise _AIFlowError("repeated_tool")
    used.append(name)


def _json_object(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if not isinstance(raw, str):
        raise _AIFlowError("invalid_model_output")
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        raise _AIFlowError("invalid_model_output")
    if not isinstance(parsed, dict):
        raise _AIFlowError("invalid_model_output")
    return parsed


def _validate_output(
    value: Dict[str, Any],
    *,
    plans: Sequence[Dict[str, Any]],
    recommended_plan_id: Any,
) -> Dict[str, Any]:
    if set(value) != set(AI_OUTPUT_SCHEMA["required"]):
        raise _AIFlowError("invalid_model_output")
    if value.get("recommended_plan_id") != recommended_plan_id:
        raise _AIFlowError("recommendation_mismatch")

    known_plans = {str(plan.get("id")) for plan in plans if plan.get("id") is not None}
    known_options = {
        str(option_id)
        for plan in plans
        for option_id in (plan.get("selections") or {}).values()
    }
    referenced_plans = value.get("referenced_plan_ids")
    referenced_options = value.get("referenced_option_ids")
    judgements = value.get("contextual_judgements")
    if (
        not isinstance(value.get("ranking_rationale"), str)
        or not value["ranking_rationale"].strip()
        or not isinstance(value.get("member_explanation"), str)
        or not value["member_explanation"].strip()
        or not isinstance(judgements, Mapping)
        or set(judgements) != {"flight", "activity"}
        or not all(isinstance(judgements[key], str) for key in judgements)
        or not isinstance(referenced_plans, list)
        or not isinstance(referenced_options, list)
        or not all(isinstance(item, str) for item in referenced_plans)
        or not all(isinstance(item, str) for item in referenced_options)
        or len(set(referenced_plans)) != len(referenced_plans)
        or len(set(referenced_options)) != len(referenced_options)
        or not set(referenced_plans).issubset(known_plans)
        or not set(referenced_options).issubset(known_options)
        or recommended_plan_id not in referenced_plans
    ):
        raise _AIFlowError("invalid_model_output")
    return value


def _openai_tools(tools: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{
        "type": "function",
        "name": tool["name"],
        "description": tool["description"],
        "parameters": tool["input_schema"],
        "strict": True,
    } for tool in tools]


async def _run_openai(
    client: Any,
    mcp_client: Any,
    tools: Sequence[Dict[str, Any]],
    *,
    model: str,
    safety_identifier: str,
    expected_tool_names: Sequence[str] = TOOL_NAMES,
    output_schema: Mapping[str, Any] = AI_OUTPUT_SCHEMA,
    schema_name: str = "tripshield_ai_insight",
    instructions: str = _SYSTEM_PROMPT,
    user_prompt: str = "Inspect the MCP planning snapshot and explain the deterministic recommendation.",
) -> Tuple[Dict[str, Any], List[str]]:
    used: List[str] = []
    previous_response_id: Optional[str] = None
    next_input: Any = user_prompt
    api_tools = _openai_tools(tools)

    for _round in range(MAX_MODEL_ROUNDS):
        missing = [name for name in expected_tool_names if name not in used]
        kwargs: Dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": next_input,
            "tools": api_tools,
            "tool_choice": "required" if missing else "none",
            "reasoning": {"effort": "low"},
            "safety_identifier": safety_identifier,
        }
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id
        if not missing:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": dict(output_schema),
                }
            }
        response = await _maybe_await(client.responses.create(**kwargs))
        previous_response_id = _value(response, "id", previous_response_id)
        calls = [
            item for item in (_value(response, "output", []) or [])
            if _value(item, "type") == "function_call"
        ]
        if calls:
            outputs = []
            for call in calls:
                name = _value(call, "name")
                _record_tool(name, used, expected_tool_names)
                arguments = _json_object(_value(call, "arguments", "{}"))
                result = await _call_mcp_tool(mcp_client, name, arguments)
                outputs.append({
                    "type": "function_call_output",
                    "call_id": _value(call, "call_id", _value(call, "id")),
                    "output": json.dumps(result, separators=(",", ":"), default=str),
                })
            next_input = outputs
            continue
        if missing:
            raise _AIFlowError("incomplete_tools")
        parsed = _value(response, "output_parsed")
        if parsed is None:
            parsed = _value(response, "output_text", "")
        return _json_object(parsed), used
    raise _AIFlowError("model_round_limit")


def _anthropic_tools(tools: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{
        "name": tool["name"],
        "description": tool["description"],
        "input_schema": tool["input_schema"],
    } for tool in tools]


async def _run_anthropic(
    client: Any,
    mcp_client: Any,
    tools: Sequence[Dict[str, Any]],
    *,
    model: str,
    safety_identifier: str,
    expected_tool_names: Sequence[str] = TOOL_NAMES,
    output_schema: Mapping[str, Any] = AI_OUTPUT_SCHEMA,
    instructions: str = _SYSTEM_PROMPT,
    user_prompt: str = "Inspect the MCP snapshot and explain the deterministic recommendation.",
) -> Tuple[Dict[str, Any], List[str]]:
    used: List[str] = []
    messages: List[Dict[str, Any]] = [{
        "role": "user",
        "content": user_prompt,
    }]

    for _round in range(MAX_MODEL_ROUNDS):
        missing = [name for name in expected_tool_names if name not in used]
        kwargs: Dict[str, Any] = {
            "model": model,
            "max_tokens": 1800,
            "system": instructions,
            "messages": messages,
            "tools": _anthropic_tools(tools),
            "tool_choice": {"type": "any"} if missing else {"type": "none"},
            "metadata": {"user_id": safety_identifier},
        }
        if not missing:
            kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": dict(output_schema)}
            }
        response = await _maybe_await(client.messages.create(**kwargs))
        content = _value(response, "content", []) or []
        calls = [block for block in content if _value(block, "type") == "tool_use"]
        if calls:
            messages.append({"role": "assistant", "content": content})
            results = []
            for call in calls:
                name = _value(call, "name")
                _record_tool(name, used, expected_tool_names)
                arguments = _value(call, "input", {})
                result = await _call_mcp_tool(mcp_client, name, arguments)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": _value(call, "id"),
                    "content": json.dumps(result, separators=(",", ":"), default=str),
                })
            messages.append({"role": "user", "content": results})
            continue
        if missing:
            raise _AIFlowError("incomplete_tools")
        text = "".join(
            str(_value(block, "text", ""))
            for block in content
            if _value(block, "type") == "text"
        )
        return _json_object(text), used
    raise _AIFlowError("model_round_limit")


def _provider_client(provider: str, api_key: str, timeout: float) -> Any:
    if provider == "anthropic":
        if _anthropic is None:
            raise _AIFlowError("provider_sdk_unavailable")
        return _anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout)
    if provider == "openai":
        if _openai is None or not hasattr(_openai, "AsyncOpenAI"):
            raise _AIFlowError("provider_sdk_unavailable")
        return _openai.AsyncOpenAI(api_key=api_key, timeout=timeout)
    raise _AIFlowError("invalid_provider")


def _exception_code(exc: BaseException) -> str:
    if isinstance(exc, _AIFlowError):
        return exc.code
    name = type(exc).__name__.lower()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in name:
        return "timeout"
    if "authentication" in name or "permission" in name:
        return "authentication"
    if "ratelimit" in name or "rate_limit" in name:
        return "rate_limit"
    return "provider_error"


async def run_structured_agent(
    *,
    role: str,
    bound_mcp: Any,
    output_schema: Mapping[str, Any],
    schema_name: str,
    instructions: str,
    user_prompt: str,
    safety_identifier: str = "demo",
    output_validator: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    provider_client: Optional[Any] = None,
    mcp_client_factory: Optional[Any] = None,
    env: Optional[Mapping[str, str]] = None,
    timeout_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Run one role-scoped model agent and return sanitized provenance.

    The caller prepares an immutable MCP snapshot and supplies the semantic
    validator. Provider failures never escape this boundary; cancellation still
    belongs to the request owner and is re-raised.
    """

    started = time.monotonic()
    selected = _selection(env)
    result: Dict[str, Any] = {
        "role": role,
        "status": "failed",
        "provider": selected.get("provider"),
        "model": selected.get("model"),
        "transport": "in_process",
        "tools_used": [],
        "output": None,
        "latency_ms": 0,
        "error_code": None,
    }
    if selected.get("error_code"):
        result.update(status="disabled", error_code=selected["error_code"])
        result["latency_ms"] = round((time.monotonic() - started) * 1000)
        return result

    expected_names = tuple(getattr(bound_mcp, "tool_names", ()))
    if not expected_names:
        result.update(status="failed", error_code="invalid_mcp_tools")
        return result

    try:
        deadline = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(_env(env).get("AI_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
        )
        if deadline <= 0:
            deadline = DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        deadline = DEFAULT_TIMEOUT_SECONDS

    provider = str(selected["provider"])
    model = str(selected["model"])

    async def run() -> Tuple[Dict[str, Any], List[str]]:
        client = provider_client or _provider_client(provider, selected["api_key"], deadline)
        async with _mcp_context(bound_mcp, mcp_client_factory) as mcp_client:
            tools = await _discover_tools(mcp_client, expected_names)
            if provider == "anthropic":
                return await _run_anthropic(
                    client,
                    mcp_client,
                    tools,
                    model=model,
                    safety_identifier=safety_identifier,
                    expected_tool_names=expected_names,
                    output_schema=output_schema,
                    instructions=instructions,
                    user_prompt=user_prompt,
                )
            return await _run_openai(
                client,
                mcp_client,
                tools,
                model=model,
                safety_identifier=safety_identifier,
                expected_tool_names=expected_names,
                output_schema=output_schema,
                schema_name=schema_name,
                instructions=instructions,
                user_prompt=user_prompt,
            )

    try:
        raw, used = await asyncio.wait_for(run(), timeout=deadline)
        try:
            validated = output_validator(raw) if output_validator else raw
        except (KeyError, TypeError, ValueError) as exc:
            raise _AIFlowError("invalid_model_output") from exc
        if not isinstance(validated, Mapping):
            raise _AIFlowError("invalid_model_output")
        result.update(
            status="generated",
            tools_used=used,
            output=dict(validated),
            error_code=None,
        )
    except BaseException as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        result.update(status="failed", error_code=_exception_code(exc), tools_used=[])
    result["latency_ms"] = round((time.monotonic() - started) * 1000)
    return result


async def generate_ai_insight(
    *,
    graph: Dict[str, Any],
    plans: List[Dict[str, Any]],
    ranking: Dict[str, Any],
    member_history: Dict[str, Any],
    safety_identifier: str = "demo",
    provider_client: Optional[Any] = None,
    mcp_client_factory: Optional[Any] = None,
    env: Optional[Mapping[str, str]] = None,
    timeout_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Generate a validated explanation or a complete safe fallback object.

    ``graph``, ``plans``, ``ranking`` and ``member_history`` are plain JSON
    values. Inputs are copied into a request-local MCP snapshot. Provider and
    MCP factories are injectable for offline tests; production callers omit
    them and use the installed SDKs.
    """

    started = time.monotonic()
    selected = _selection(env)
    result = _result_base(selected.get("provider"), selected.get("model"))
    result["recommended_plan_id"] = ranking.get("recommended_plan_id")
    if selected.get("error_code"):
        result.update(status="disabled", error_code=selected["error_code"])
        result["latency_ms"] = round((time.monotonic() - started) * 1000)
        return result

    provider = str(selected["provider"])
    model = str(selected["model"])
    try:
        deadline = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(_env(env).get("AI_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
        )
        if deadline <= 0:
            deadline = DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        deadline = DEFAULT_TIMEOUT_SECONDS

    async def run() -> Tuple[Dict[str, Any], List[str]]:
        bound = create_mcp_server(
            graph=graph,
            plans=plans,
            ranking=ranking,
            member_history=member_history,
        )
        client = provider_client or _provider_client(provider, selected["api_key"], deadline)
        async with _mcp_context(bound, mcp_client_factory) as mcp_client:
            tools = await _discover_tools(mcp_client)
            if provider == "anthropic":
                return await _run_anthropic(
                    client, mcp_client, tools, model=model,
                    safety_identifier=safety_identifier,
                )
            return await _run_openai(
                client, mcp_client, tools, model=model,
                safety_identifier=safety_identifier,
            )

    try:
        raw, used = await asyncio.wait_for(run(), timeout=deadline)
        validated = _validate_output(
            raw,
            plans=plans,
            recommended_plan_id=ranking.get("recommended_plan_id"),
        )
        result.update({
            "status": "generated",
            "tools_used": used,
            "recommended_plan_id": validated["recommended_plan_id"],
            "ranking_rationale": validated["ranking_rationale"],
            "member_explanation": validated["member_explanation"],
            "contextual_judgements": dict(validated["contextual_judgements"]),
            "error_code": None,
        })
    except BaseException as exc:
        # Cancellation belongs to the caller; all operational/model failures
        # become sanitized fallback metadata and never break deterministic plan.
        if isinstance(exc, asyncio.CancelledError):
            raise
        result.update(status="failed", error_code=_exception_code(exc), tools_used=[])
    result["latency_ms"] = round((time.monotonic() - started) * 1000)
    return result
