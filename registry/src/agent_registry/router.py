"""Router MCP server for discovering and chatting with registered A2A agents."""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import a2a_client, cards, db, health, search as search_mod
from .weave_tracing import tracer

DEFAULT_LIMIT = 10
MAX_LIMIT = int(os.getenv("AGENT_ROUTER_MAX_LIMIT", "50"))
DEFAULT_A2A_TIMEOUT_SECONDS = float(os.getenv("A2A_CLIENT_TIMEOUT", "120"))

mcp = FastMCP(
    "agent-router",
    instructions=(
        "Discover A2A agents from the registry and chat with them over A2A. "
        "Use search_agents to find candidate agents by tags, name, skill text, "
        "or description. Use ask_agents to discover up to 10 matching agents by "
        "default and ask each one once in parallel. Every result includes a "
        "conversation_id and preserves that agent's A2A context_id. If the "
        "first response does not give enough information, the MCP client should "
        "call continue_agent_conversation or continue_agent_conversations with "
        "the returned conversation_id values. The router does not decide when "
        "follow-up is needed; the client does."
    ),
)

_background_tasks: set[asyncio.Task[None]] = set()


def _refresh_in_background() -> None:
    task = asyncio.create_task(health.refresh_stale())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _limit(value: int) -> int:
    return max(1, min(value, MAX_LIMIT))


def _active_records() -> list[dict[str, Any]]:
    return [record for record in db.list_agents() if record["status"] == "active"]


def _record_name(record: dict[str, Any]) -> str | None:
    name = record["card"].get("name")
    return name if isinstance(name, str) else None


def _conversation_hint() -> str:
    return (
        "If any answer is incomplete, call continue_agent_conversation with that "
        "result's conversation_id. The router will send the follow-up with the "
        "saved A2A context_id."
    )


def _error_result(
    *,
    agent: dict[str, Any] | None,
    conversation_id: str | None,
    message: str,
    error: str,
) -> dict[str, Any]:
    return {
        "agent": cards.summarize(agent) if agent else None,
        "conversation_id": conversation_id,
        "message": message,
        "answer": None,
        "context_id": None,
        "task_id": None,
        "turn_index": None,
        "status": "error",
        "error": error,
    }


def _turn_payload(
    *,
    conversation: dict[str, Any],
    turn: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "agent": {
            "id": conversation["agent_id"],
            "name": conversation["agent_name"],
            "endpoint": conversation["endpoint"],
        },
        "conversation_id": conversation["id"],
        "weave_trace_id": conversation.get("weave_trace_id"),
        "message": turn["request"],
        "answer": result.get("answer"),
        "context_id": result.get("context_id"),
        "task_id": result.get("task_id"),
        "turn_index": turn["turn_index"],
        "status": "ok",
        "error": None,
    }


def _discover_records(
    *,
    query: str,
    tags: list[str] | None,
    limit: int,
    agent_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    limit = _limit(limit)
    if agent_ids:
        selected: list[dict[str, Any]] = []
        for agent_id in agent_ids[:limit]:
            record = db.get_agent(agent_id)
            if record is not None and record["status"] == "active":
                selected.append(record)
        return selected

    records = _active_records()
    return [
        record
        for record, _score in search_mod.search(
            records,
            query,
            tags=tags,
            limit=limit,
        )
    ]


async def _send_conversation_turn(
    *,
    conversation: dict[str, Any],
    message: str,
    timeout_seconds: float = DEFAULT_A2A_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    result = await a2a_client.send_message(
        endpoint=conversation["endpoint"],
        text=message,
        context_id=conversation.get("a2a_context_id"),
        timeout_seconds=timeout_seconds,
    )
    db.update_conversation_context(
        conversation["id"],
        a2a_context_id=result.get("context_id"),
        task_id=result.get("task_id"),
    )
    turn = db.append_conversation_turn(
        conversation_id=conversation["id"],
        request=message,
        response=result.get("answer"),
        raw_response=result.get("raw_result"),
        a2a_context_id=result.get("context_id"),
        task_id=result.get("task_id"),
    )
    updated = db.get_conversation(conversation["id"])
    tracer.log_turn(
        conversation=updated or conversation,
        turn_index=turn["turn_index"],
        request=message,
        result=result,
    )
    return _turn_payload(conversation=updated or conversation, turn=turn, result=result)


async def _start_conversation(
    *,
    record: dict[str, Any],
    message: str,
    timeout_seconds: float = DEFAULT_A2A_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    endpoint = record.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        return _error_result(
            agent=record,
            conversation_id=None,
            message=message,
            error="Agent has no A2A endpoint in its card.",
        )

    conversation = db.create_conversation(
        agent_id=record["id"],
        agent_name=_record_name(record),
        endpoint=endpoint,
        card_url=record.get("card_url"),
    )
    trace = tracer.start_conversation(
        conversation_id=conversation["id"],
        agent_id=record["id"],
        agent_name=_record_name(record),
        endpoint=endpoint,
        first_message=message,
    )
    db.set_conversation_trace(
        conversation["id"],
        weave_trace_id=trace["weave_trace_id"],
        weave_root_call_id=trace["weave_root_call_id"],
    )
    conversation = db.get_conversation(conversation["id"]) or conversation

    try:
        return await _send_conversation_turn(
            conversation=conversation,
            message=message,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        db.update_conversation_context(
            conversation["id"],
            a2a_context_id=None,
            task_id=None,
            status="error",
        )
        turn = db.append_conversation_turn(
            conversation_id=conversation["id"],
            request=message,
            response=None,
            raw_response=None,
            a2a_context_id=None,
            task_id=None,
            error=error,
        )
        updated = db.get_conversation(conversation["id"]) or conversation
        tracer.log_turn(
            conversation=updated,
            turn_index=turn["turn_index"],
            request=message,
            result=None,
            error=error,
        )
        return _error_result(
            agent=record,
            conversation_id=conversation["id"],
            message=message,
            error=error,
        )


async def _continue_conversation(
    *,
    conversation_id: str,
    message: str,
    timeout_seconds: float = DEFAULT_A2A_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    conversation = db.get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"No conversation with id {conversation_id!r}")
    if conversation["status"] == "completed":
        raise ValueError(f"Conversation {conversation_id!r} is already completed")

    try:
        return await _send_conversation_turn(
            conversation=conversation,
            message=message,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        db.update_conversation_context(
            conversation_id,
            a2a_context_id=conversation.get("a2a_context_id"),
            task_id=conversation.get("task_id"),
            status="error",
        )
        turn = db.append_conversation_turn(
            conversation_id=conversation_id,
            request=message,
            response=None,
            raw_response=None,
            a2a_context_id=conversation.get("a2a_context_id"),
            task_id=conversation.get("task_id"),
            error=error,
        )
        updated = db.get_conversation(conversation_id) or conversation
        tracer.log_turn(
            conversation=updated,
            turn_index=turn["turn_index"],
            request=message,
            result=None,
            error=error,
        )
        return {
            "agent": {
                "id": conversation["agent_id"],
                "name": conversation["agent_name"],
                "endpoint": conversation["endpoint"],
            },
            "conversation_id": conversation_id,
            "weave_trace_id": conversation.get("weave_trace_id"),
            "message": message,
            "answer": None,
            "context_id": conversation.get("a2a_context_id"),
            "task_id": conversation.get("task_id"),
            "turn_index": turn["turn_index"],
            "status": "error",
            "error": error,
        }


@mcp.tool()
async def search_agents(
    query: str,
    tags: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Find active registered agents by text search, defaulting to 10 results.

    Text search ranks matches from tags, agent name, skill names,
    descriptions, and examples. Use ask_agents to start A2A conversations
    with the results.
    """
    _refresh_in_background()
    records = _active_records()
    results = search_mod.search(records, query, tags=tags, limit=_limit(limit))
    return [cards.summarize(record, score) for record, score in results]


@mcp.tool()
async def get_agent_card(agent_id: str) -> dict[str, Any]:
    """Return the full registry record for a discovered agent."""
    record = db.get_agent(agent_id)
    if record is None:
        raise ValueError(f"No agent with id {agent_id!r}")
    return record


@mcp.tool()
async def ask_agents(
    message: str,
    query: str | None = None,
    tags: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    agent_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Discover matching agents and ask each one once in parallel.

    Multi-turn is client-driven: each result includes a conversation_id. If the
    client needs more information after turn 1, call continue_agent_conversation
    or continue_agent_conversations with those ids. The router preserves each
    remote agent's A2A context_id, so follow-ups continue the same conversation.

    Args:
        message: The message to send to every selected A2A agent.
        query: Optional discovery query. Defaults to message when omitted.
        tags: Optional tag filter; every listed tag must be present.
        limit: Maximum agents to discover. Defaults to 10.
        agent_ids: Optional explicit agent ids; when provided, bypasses search.
    """
    _refresh_in_background()
    discovery_query = query or message
    records = _discover_records(
        query=discovery_query,
        tags=tags,
        limit=limit,
        agent_ids=agent_ids,
    )
    results = await asyncio.gather(
        *[_start_conversation(record=record, message=message) for record in records]
    )
    return {
        "query": discovery_query,
        "message": message,
        "limit": _limit(limit),
        "matched_agents": [cards.summarize(record) for record in records],
        "results": results,
        "followup": _conversation_hint(),
    }


@mcp.tool()
async def continue_agent_conversation(
    conversation_id: str,
    message: str,
) -> dict[str, Any]:
    """Send a follow-up to one prior A2A conversation.

    The router uses the saved A2A context_id for this conversation_id. This is
    the tool the MCP client should call when a previous answer is incomplete or
    needs clarification.
    """
    result = await _continue_conversation(
        conversation_id=conversation_id,
        message=message,
    )
    result["followup"] = _conversation_hint()
    return result


@mcp.tool()
async def continue_agent_conversations(
    conversation_ids: list[str],
    message: str,
) -> dict[str, Any]:
    """Send the same follow-up to multiple prior A2A conversations in parallel.

    Each conversation keeps its own saved A2A context_id. Use this when the MCP
    client wants more detail from several agents after the first fan-out turn.
    """
    async def _safe_continue(conversation_id: str) -> dict[str, Any]:
        try:
            return await _continue_conversation(
                conversation_id=conversation_id,
                message=message,
            )
        except Exception as exc:
            return _error_result(
                agent=None,
                conversation_id=conversation_id,
                message=message,
                error=f"{type(exc).__name__}: {exc}",
            )

    results = await asyncio.gather(
        *[_safe_continue(conversation_id) for conversation_id in conversation_ids]
    )
    return {
        "message": message,
        "results": results,
        "followup": _conversation_hint(),
    }


@mcp.tool()
async def get_agent_conversation(
    conversation_id: str,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Return a saved router conversation and its local turn transcript."""
    conversation = db.get_conversation(conversation_id, include_raw=include_raw)
    if conversation is None:
        raise ValueError(f"No conversation with id {conversation_id!r}")
    return conversation


@mcp.tool()
async def list_agent_conversations(
    agent_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List saved router conversations, most recently updated first."""
    return db.list_conversations(agent_id=agent_id, status=status, limit=_limit(limit))


@mcp.tool()
async def finish_agent_conversation(conversation_id: str) -> dict[str, Any]:
    """Mark a conversation completed and close its Weave conversation trace.

    This is optional. Continue tools keep working until this is called, and the
    client should call it only when no more follow-ups are expected.
    """
    conversation = db.get_conversation(conversation_id, include_raw=False)
    if conversation is None:
        raise ValueError(f"No conversation with id {conversation_id!r}")
    db.finish_conversation(conversation_id)
    completed = db.get_conversation(conversation_id, include_raw=False)
    if completed is not None:
        tracer.finish_conversation(
            conversation=completed,
            output={
                "conversation_id": conversation_id,
                "turns": completed.get("turns", []),
                "status": completed["status"],
            },
        )
        return completed
    raise ValueError(f"No conversation with id {conversation_id!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Router MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="stdio for local clients, http for a hosted Streamable HTTP endpoint",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()

    if args.transport == "http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
