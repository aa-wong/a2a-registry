import inspect
import os
from typing import Any

import httpx
from a2a.client import A2ACardResolver
from a2a_bridge import ask_a2a_agent
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

try:
    from google.protobuf.json_format import MessageToDict
except ImportError:  # pragma: no cover - optional protobuf helper
    MessageToDict = None

load_dotenv()

mcp = FastMCP(
    os.getenv("MCP_SERVER_NAME", "a2a-agent-bridge"),
    instructions=(
        "Use get_agent_card to inspect the configured A2A Agent Card. "
        "Use ask_agent to communicate with the configured A2A agent. "
        "If ask_agent returns a context_id, pass it back on follow-up questions. "
    ),
)


@mcp.tool()
async def get_agent_card() -> dict[str, Any] | str:
    """Return the configured A2A Agent Card."""
    base_url = os.getenv("A2A_BASE_URL", "http://127.0.0.1:9999")
    timeout_seconds = float(os.getenv("A2A_CLIENT_TIMEOUT", "180"))

    async with httpx.AsyncClient(timeout=timeout_seconds) as httpx_client:
        resolver = A2ACardResolver(
            httpx_client=httpx_client,
            base_url=base_url,
        )
        agent_card = await resolver.get_agent_card()

    return _agent_card_to_mcp_payload(agent_card)


@mcp.tool()
async def ask_agent(question: str, context_id: str | None = None) -> dict[str, Any] | str:
    """Ask the configured A2A agent a question."""
    kwargs: dict[str, Any] = {}
    if _ask_a2a_agent_accepts("context_id") and context_id:
        kwargs["context_id"] = context_id
    if _ask_a2a_agent_accepts("return_metadata"):
        kwargs["return_metadata"] = True

    response = await ask_a2a_agent(question, **kwargs)
    if isinstance(response, dict):
        return response

    return {
        "answer": response,
        "context_id": context_id,
        "task_id": None,
    }


def _ask_a2a_agent_accepts(keyword: str) -> bool:
    signature = inspect.signature(ask_a2a_agent)
    return keyword in signature.parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


def _agent_card_to_mcp_payload(agent_card: object) -> dict[str, Any] | str:
    if MessageToDict is not None and hasattr(agent_card, "DESCRIPTOR"):
        return MessageToDict(agent_card, preserving_proto_field_name=True)

    if hasattr(agent_card, "model_dump"):
        return agent_card.model_dump(mode="json", exclude_none=True)

    if hasattr(agent_card, "dict"):
        return agent_card.dict(exclude_none=True)

    return str(agent_card)


if __name__ == "__main__":
    mcp.run()
