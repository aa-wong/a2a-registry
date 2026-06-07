"""End-to-end smoke test for the agent registry.

Exercises the full hackathon demo loop with zero hardcoded knowledge of
the target agent beyond its registration URL:

    1. register_agent  — fetch + validate + store the live portfolio agent
    2. search_agents   — discover it from a natural-language query
    3. get_agent_card  — pull the full card, extract the A2A endpoint
    4. A2A message/send — two-turn conversation proving contextId state

Run:  uv run python tests/smoke_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid

# Isolated throwaway DB so the smoke test never pollutes the real registry.
os.environ["AGENT_REGISTRY_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="agent-registry-smoke-"), "registry.db"
)

import httpx

from agent_registry import server

PORTFOLIO_URL = "https://aaronwongellis.com"


async def a2a_send(endpoint: str, text: str, context_id: str | None = None) -> dict:
    """Minimal A2A client: JSON-RPC message/send with optional contextId."""
    message: dict = {
        "role": "user",
        "messageId": str(uuid.uuid4()),
        "parts": [{"text": text}],
    }
    if context_id:
        message["contextId"] = context_id
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            endpoint,
            json={
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "message/send",
                "params": {"message": message},
            },
        )
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"A2A error: {body['error']}")
    return body["result"]


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))
    return ok


async def main() -> int:
    failures = 0

    print("\n[1/4] register_agent")
    registered = await server.register_agent(PORTFOLIO_URL)
    failures += not check(
        "agent registered",
        registered["id"].startswith("agt_"),
        f"{registered['name']} ({registered['id']})",
    )
    failures += not check("status active", registered["status"] == "active")

    print("\n[2/4] search_agents")
    results = await server.search_agents(
        "I need an agent that can answer questions about Aaron's career and projects"
    )
    found = results[0] if results else None
    failures += not check(
        "portfolio agent found",
        bool(found) and found["id"] == registered["id"],
        f"score={found['score']}" if found else "no results",
    )
    miss = await server.search_agents("real-time weather forecasts for sailing")
    failures += not check("unrelated query misses", miss == [], f"{len(miss)} results")

    print("\n[3/4] get_agent_card")
    record = await server.get_agent_card(registered["id"])
    endpoint = record["endpoint"]
    failures += not check("endpoint present", bool(endpoint), endpoint or "missing")
    failures += not check(
        "card has skills", bool(record["card"].get("skills")),
        ", ".join(s["id"] for s in record["card"]["skills"]),
    )

    print("\n[4/4] A2A two-turn conversation")
    turn1 = await a2a_send(endpoint, "In one sentence, what does Aaron do?")
    reply1 = " ".join(p.get("text", "") for p in turn1["parts"])
    context_id = turn1.get("contextId")
    failures += not check("turn 1 answered", bool(reply1), reply1[:90] + "...")
    failures += not check("contextId returned", bool(context_id), context_id)

    turn2 = await a2a_send(
        endpoint, "What was the last company you mentioned?", context_id
    )
    reply2 = " ".join(p.get("text", "") for p in turn2["parts"])
    failures += not check(
        "turn 2 carried context",
        turn2.get("contextId") == context_id and bool(reply2),
        reply2[:90] + "...",
    )

    print(f"\n{'SMOKE TEST PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
