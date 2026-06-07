"""Liveness checks — delist dead agents.

An agent is `active` while its card URL answers 200, `offline` once it
stops. Checks run lazily: search/list fire a background refresh of any
record not seen within MAX_AGE_SECONDS, so results are at most one call
behind reality and no scheduler is needed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

from . import db

MAX_AGE_SECONDS = 300
CHECK_TIMEOUT = 5.0


def _is_stale(record: dict[str, Any]) -> bool:
    last_seen = record.get("last_seen_at")
    if not last_seen:
        return True
    try:
        seen = datetime.fromisoformat(last_seen)
    except ValueError:
        return True
    age = (datetime.now(timezone.utc) - seen).total_seconds()
    return age > MAX_AGE_SECONDS


async def check_agent(record: dict[str, Any]) -> str:
    """Ping the agent's card URL; update and return its status."""
    try:
        async with httpx.AsyncClient(
            timeout=CHECK_TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(record["card_url"])
        alive = resp.status_code == 200
    except httpx.HTTPError:
        alive = False
    status = "active" if alive else "offline"
    db.update_status(record["id"], status, seen=alive)
    return status


async def refresh_stale() -> None:
    """Re-check every record whose last check is older than MAX_AGE_SECONDS."""
    stale = [r for r in db.list_agents() if _is_stale(r)]
    if stale:
        await asyncio.gather(*(check_agent(r) for r in stale))
