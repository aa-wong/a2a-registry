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

CREATE TABLE IF NOT EXISTS conversations (
    id                 TEXT PRIMARY KEY,
    agent_id           TEXT NOT NULL,
    agent_name         TEXT,
    endpoint           TEXT NOT NULL,
    card_url           TEXT,
    a2a_context_id     TEXT,
    task_id            TEXT,
    weave_trace_id     TEXT,
    weave_root_call_id TEXT,
    status             TEXT NOT NULL DEFAULT 'active',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_index      INTEGER NOT NULL,
    request         TEXT NOT NULL,
    response        TEXT,
    raw_response    TEXT,
    error           TEXT,
    a2a_context_id  TEXT,
    task_id         TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(conversation_id, turn_index),
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);
"""


def db_path() -> Path:
    return Path(os.environ.get("AGENT_REGISTRY_DB", DEFAULT_DB))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
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


def _to_turn(row: sqlite3.Row, include_raw: bool = False) -> dict[str, Any]:
    turn = {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "turn_index": row["turn_index"],
        "request": row["request"],
        "response": row["response"],
        "error": row["error"],
        "a2a_context_id": row["a2a_context_id"],
        "task_id": row["task_id"],
        "created_at": row["created_at"],
    }
    if include_raw:
        raw_response = row["raw_response"]
        if raw_response:
            try:
                turn["raw_response"] = json.loads(raw_response)
            except ValueError:
                turn["raw_response"] = raw_response
        else:
            turn["raw_response"] = None
    return turn


def _to_conversation(
    row: sqlite3.Row,
    turns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    conversation = {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "agent_name": row["agent_name"],
        "endpoint": row["endpoint"],
        "card_url": row["card_url"],
        "a2a_context_id": row["a2a_context_id"],
        "task_id": row["task_id"],
        "weave_trace_id": row["weave_trace_id"],
        "weave_root_call_id": row["weave_root_call_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if turns is not None:
        conversation["turns"] = turns
    return conversation


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


def create_conversation(
    *,
    agent_id: str,
    agent_name: str | None,
    endpoint: str,
    card_url: str | None,
    weave_trace_id: str | None = None,
    weave_root_call_id: str | None = None,
) -> dict[str, Any]:
    conversation_id = f"conv_{uuid.uuid4().hex[:12]}"
    timestamp = now_iso()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO conversations (
                id, agent_id, agent_name, endpoint, card_url,
                weave_trace_id, weave_root_call_id, status,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                conversation_id,
                agent_id,
                agent_name,
                endpoint,
                card_url,
                weave_trace_id,
                weave_root_call_id,
                timestamp,
                timestamp,
            ),
        )
    return get_conversation(conversation_id)  # type: ignore[return-value]


def set_conversation_trace(
    conversation_id: str,
    *,
    weave_trace_id: str | None,
    weave_root_call_id: str | None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE conversations
            SET weave_trace_id = ?, weave_root_call_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (weave_trace_id, weave_root_call_id, now_iso(), conversation_id),
        )


def update_conversation_context(
    conversation_id: str,
    *,
    a2a_context_id: str | None,
    task_id: str | None,
    status: str = "active",
) -> None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT a2a_context_id, task_id FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"No conversation with id {conversation_id!r}")
        conn.execute(
            """
            UPDATE conversations
            SET a2a_context_id = ?, task_id = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                a2a_context_id or row["a2a_context_id"],
                task_id or row["task_id"],
                status,
                now_iso(),
                conversation_id,
            ),
        )


def append_conversation_turn(
    *,
    conversation_id: str,
    request: str,
    response: str | None,
    raw_response: dict[str, Any] | list[Any] | str | None,
    a2a_context_id: str | None,
    task_id: str | None,
    error: str | None = None,
) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(turn_index), 0) AS max_turn "
            "FROM conversation_turns WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        turn_index = int(row["max_turn"]) + 1
        turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        if isinstance(raw_response, str) or raw_response is None:
            raw_response_json = raw_response
        else:
            raw_response_json = json.dumps(raw_response)
        conn.execute(
            """
            INSERT INTO conversation_turns (
                id, conversation_id, turn_index, request, response,
                raw_response, error, a2a_context_id, task_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_id,
                conversation_id,
                turn_index,
                request,
                response,
                raw_response_json,
                error,
                a2a_context_id,
                task_id,
                now_iso(),
            ),
        )
        turn = conn.execute(
            "SELECT * FROM conversation_turns WHERE id = ?", (turn_id,)
        ).fetchone()
        return _to_turn(turn)  # type: ignore[arg-type]


def get_conversation(
    conversation_id: str,
    *,
    include_turns: bool = True,
    include_raw: bool = False,
) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if row is None:
            return None
        turns = None
        if include_turns:
            turn_rows = conn.execute(
                "SELECT * FROM conversation_turns "
                "WHERE conversation_id = ? ORDER BY turn_index",
                (conversation_id,),
            ).fetchall()
            turns = [_to_turn(turn, include_raw=include_raw) for turn in turn_rows]
        return _to_conversation(row, turns)


def list_conversations(
    *,
    status: str | None = None,
    agent_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if agent_id:
        clauses.append("agent_id = ?")
        params.append(agent_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM conversations {where} ORDER BY updated_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [_to_conversation(row) for row in rows]


def finish_conversation(conversation_id: str, status: str = "completed") -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE conversations SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), conversation_id),
        )
