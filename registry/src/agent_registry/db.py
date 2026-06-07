"""SQLite persistence for registry records.

Each record wraps a provider-authored A2A Agent Card (stored verbatim as
JSON) with registry-side metadata: id, status, timestamps, denormalized
tags. Connections are opened per operation — cheap for SQLite and safe
across the async server's worker threads.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(__file__).resolve().parents[2] / "registry.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id            TEXT PRIMARY KEY,
    card_url      TEXT NOT NULL UNIQUE,
    endpoint      TEXT,
    card          TEXT NOT NULL,
    tags          TEXT NOT NULL DEFAULT '[]',
    status        TEXT NOT NULL DEFAULT 'active',
    registered_at TEXT NOT NULL,
    last_seen_at  TEXT
);
"""


def db_path() -> Path:
    return Path(os.environ.get("AGENT_REGISTRY_DB", DEFAULT_DB))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    return conn


def _to_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "card_url": row["card_url"],
        "endpoint": row["endpoint"],
        "card": json.loads(row["card"]),
        "tags": json.loads(row["tags"]),
        "status": row["status"],
        "registered_at": row["registered_at"],
        "last_seen_at": row["last_seen_at"],
    }


def upsert_agent(
    card_url: str,
    endpoint: str | None,
    card: dict[str, Any],
    tags: list[str],
) -> dict[str, Any]:
    """Insert a new record, or refresh the card on an existing card_url."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, registered_at FROM agents WHERE card_url = ?", (card_url,)
        ).fetchone()
        agent_id = row["id"] if row else f"agt_{uuid.uuid4().hex[:12]}"
        registered_at = row["registered_at"] if row else now_iso()
        conn.execute(
            """
            INSERT INTO agents (id, card_url, endpoint, card, tags, status,
                                registered_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(card_url) DO UPDATE SET
                endpoint = excluded.endpoint,
                card = excluded.card,
                tags = excluded.tags,
                status = 'active',
                last_seen_at = excluded.last_seen_at
            """,
            (
                agent_id,
                card_url,
                endpoint,
                json.dumps(card),
                json.dumps(tags),
                registered_at,
                now_iso(),
            ),
        )
    # Read back on a fresh connection, after the insert has committed.
    return get_agent(agent_id)  # type: ignore[return-value]


def get_agent(agent_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return _to_record(row) if row else None


def list_agents(status: str | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM agents WHERE status = ? ORDER BY registered_at",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agents ORDER BY registered_at"
            ).fetchall()
        return [_to_record(r) for r in rows]


def update_status(agent_id: str, status: str, seen: bool) -> None:
    with _connect() as conn:
        if seen:
            conn.execute(
                "UPDATE agents SET status = ?, last_seen_at = ? WHERE id = ?",
                (status, now_iso(), agent_id),
            )
        else:
            conn.execute(
                "UPDATE agents SET status = ? WHERE id = ?", (status, agent_id)
            )


def delete_agent(agent_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        return cur.rowcount > 0
