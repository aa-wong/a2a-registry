"""Agent Registry MCP server.

Exposes the registry as MCP tools so any MCP-speaking agent can discover
A2A agents at runtime, then talk to them directly over A2A. The registry
holds no conversation state — it is pure discovery.

Run (stdio, for `claude mcp add`):   agent-registry
Run (Streamable HTTP, hosted demo):  agent-registry --transport http --port 8765
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import cards, db, health, search as search_mod

mcp = FastMCP(
    "agent-registry",
    instructions=(
        "Registry of A2A agents. Use search_agents to find an agent by "
        "capability, get_agent_card for its full A2A Agent Card (including "
        "endpoint and auth schemes), then communicate with the agent "
        "directly over the A2A protocol (JSON-RPC message/send to its "
        "endpoint; echo the returned contextId to continue a conversation)."
    ),
)

# Keep references to fire-and-forget refresh tasks so they aren't GC'd.
_background_tasks: set[asyncio.Task] = set()


def _refresh_in_background() -> None:
    task = asyncio.create_task(health.refresh_stale())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@mcp.tool()
async def register_agent(card_url: str) -> dict[str, Any]:
    """Register an A2A agent by the URL of its site or agent card.

    The registry fetches /.well-known/agent-card.json (or agent.json) from
    the given URL itself — registration proves control of the domain that
    serves the card. Re-registering the same URL refreshes the stored card.

    Args:
        card_url: Site base URL (e.g. https://example.com) or a direct
            card URL ending in .json.
    """
    card, resolved_url = await cards.fetch_agent_card(card_url)
    problems = cards.validate_card(card)
    if problems:
        raise ValueError("Invalid agent card: " + "; ".join(problems))
    record = db.upsert_agent(
        card_url=resolved_url,
        endpoint=cards.extract_endpoint(card),
        card=card,
        tags=cards.extract_tags(card),
    )
    return cards.summarize(record)


@mcp.tool()
async def search_agents(
    query: str,
    tags: list[str] | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Find agents matching a natural-language description of a needed capability.

    Args:
        query: What you need done, e.g. "answer questions about Aaron's career".
        tags: Optional tag filter — only agents carrying every listed tag match.
        limit: Maximum number of results (default 5).

    Returns ranked summaries. Call get_agent_card with a result's id for
    the full card before initiating A2A communication.
    """
    _refresh_in_background()
    records = [r for r in db.list_agents() if r["status"] == "active"]
    results = search_mod.search(records, query, tags=tags, limit=limit)
    return [cards.summarize(record, score) for record, score in results]


@mcp.tool()
async def get_agent_card(agent_id: str) -> dict[str, Any]:
    """Fetch the full registry record for an agent, including its complete
    A2A Agent Card (endpoint, capabilities, auth schemes, skills).

    Args:
        agent_id: Registry id from a search_agents or list_agents result.
    """
    record = db.get_agent(agent_id)
    if record is None:
        raise ValueError(f"No agent with id {agent_id!r}")
    return record


@mcp.tool()
async def list_agents(status: str | None = None) -> list[dict[str, Any]]:
    """List registered agents, optionally filtered by status.

    Args:
        status: Optional filter — "active" or "offline".
    """
    _refresh_in_background()
    return [cards.summarize(r) for r in db.list_agents(status)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Registry MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="stdio for local clients, http for a hosted Streamable HTTP endpoint",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.transport == "http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
