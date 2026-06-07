"""Optional Redis integration for registry records and router sessions.

The SQL backend remains authoritative for the agent catalog. Redis mirrors
catalog records for fast external access and, when configured, stores live
router conversation/session state using redis-py.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from dotenv import load_dotenv

load_dotenv()

DEFAULT_PREFIX = "agent-registry"
CONNECT_TIMEOUT = float(os.getenv("AGENT_REGISTRY_REDIS_CONNECT_TIMEOUT", "0.5"))
SOCKET_TIMEOUT = float(os.getenv("AGENT_REGISTRY_REDIS_SOCKET_TIMEOUT", "1.0"))

_client: Any | None = None


def redis_url() -> str | None:
    return os.getenv("AGENT_REGISTRY_REDIS_URL") or os.getenv("REDIS_URL") or None


def redis_host() -> str | None:
    return os.getenv("AGENT_REGISTRY_REDIS_HOST") or None


def enabled() -> bool:
    return bool(redis_url() or redis_host())


def session_enabled() -> bool:
    backend = os.getenv("AGENT_REGISTRY_SESSION_BACKEND")
    if backend:
        return backend.lower() == "redis"
    return enabled()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _prefix() -> str:
    return os.getenv("AGENT_REGISTRY_REDIS_PREFIX", DEFAULT_PREFIX).strip(":")


def _key(*parts: str) -> str:
    return ":".join((_prefix(), *parts))


def _key_fragment(value: str) -> str:
    fragment = re.sub(r"[^a-z0-9_.-]+", "-", value.lower()).strip("-")
    return fragment or "unknown"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _timestamp(value: str | None) -> float:
    if not value:
        return datetime.now(timezone.utc).timestamp()
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return datetime.now(timezone.utc).timestamp()


def _session_ttl() -> int | None:
    raw = os.getenv("AGENT_REGISTRY_REDIS_SESSION_TTL_SECONDS")
    if not raw:
        return None
    try:
        ttl = int(raw)
    except ValueError:
        return None
    return ttl if ttl > 0 else None


def _get_client() -> Any | None:
    global _client
    if _client is not None:
        return _client
    if not enabled():
        return None

    import redis

    url = redis_url()
    if url:
        _client = redis.Redis.from_url(
            url,
            decode_responses=True,
            retry_on_timeout=True,
            socket_connect_timeout=CONNECT_TIMEOUT,
            socket_timeout=SOCKET_TIMEOUT,
        )
    else:
        _client = redis.Redis(
            host=redis_host(),
            port=_env_int("AGENT_REGISTRY_REDIS_PORT", 6379),
            db=_env_int("AGENT_REGISTRY_REDIS_DB", 0),
            decode_responses=True,
            username=os.getenv("AGENT_REGISTRY_REDIS_USERNAME"),
            password=os.getenv("AGENT_REGISTRY_REDIS_PASSWORD"),
            ssl=_env_bool("AGENT_REGISTRY_REDIS_SSL"),
            retry_on_timeout=True,
            socket_connect_timeout=CONNECT_TIMEOUT,
            socket_timeout=SOCKET_TIMEOUT,
        )
    return _client


def _require_client() -> Any:
    client = _get_client()
    if client is None:
        raise RuntimeError(
            "Redis session backend is selected but no Redis connection is configured"
        )
    return client


def _run(operation: Callable[[Any], None], *, strict: bool = False) -> None:
    try:
        client = _get_client()
        if client is None:
            return
        operation(client)
    except Exception:
        # Redis is an optional catalog mirror; SQL remains authoritative there.
        if strict:
            raise


def _record_hash(record: dict[str, Any]) -> dict[str, str]:
    return {
        "id": record["id"],
        "card_url": record.get("card_url") or "",
        "endpoint": record.get("endpoint") or "",
        "card": json.dumps(record.get("card") or {}),
        "tags": json.dumps(record.get("tags") or []),
        "status": record.get("status") or "",
        "registered_at": record.get("registered_at") or "",
        "last_seen_at": record.get("last_seen_at") or "",
    }


def _sync_agent(client: Any, record: dict[str, Any], *, event_type: str | None) -> None:
    agent_id = record["id"]
    agent_key = _key("agent", agent_id)
    previous_status = client.hget(agent_key, "status")
    previous_tags_raw = client.hget(agent_key, "tags")
    try:
        previous_tags = set(json.loads(previous_tags_raw or "[]"))
    except ValueError:
        previous_tags = set()
    tags = set(record.get("tags") or [])
    status = record.get("status") or "unknown"

    pipe = client.pipeline(transaction=False)
    pipe.hset(agent_key, mapping=_record_hash(record))
    pipe.sadd(_key("agents", "all"), agent_id)
    pipe.zadd(
        _key("agents", "registered"),
        {agent_id: _timestamp(record.get("registered_at"))},
    )
    if previous_status and previous_status != status:
        pipe.srem(_key("agents", "status", previous_status), agent_id)
    pipe.sadd(_key("agents", "status", status), agent_id)
    for tag in previous_tags - tags:
        pipe.srem(_key("tag", _key_fragment(tag), "agents"), agent_id)
    for tag in tags:
        pipe.sadd(_key("tag", _key_fragment(tag), "agents"), agent_id)
    if event_type:
        pipe.xadd(
            _key("events"),
            {
                "type": event_type,
                "id": agent_id,
                "at": _now_iso(),
            },
        )
    pipe.execute()


def cache_agent(record: dict[str, Any]) -> None:
    _run(lambda client: _sync_agent(client, record, event_type=None))


def cache_agents(records: list[dict[str, Any]]) -> None:
    def sync(client: Any) -> None:
        for record in records:
            _sync_agent(client, record, event_type=None)

    _run(sync)


def agent_upserted(record: dict[str, Any]) -> None:
    _run(
        lambda client: _sync_agent(client, record, event_type="agent.upserted"),
        strict=os.getenv("AGENT_REGISTRY_REDIS_STRICT") == "1",
    )


def agent_status_updated(record: dict[str, Any]) -> None:
    _run(
        lambda client: _sync_agent(client, record, event_type="agent.status_updated"),
        strict=os.getenv("AGENT_REGISTRY_REDIS_STRICT") == "1",
    )


def agent_deleted(record: dict[str, Any]) -> None:
    def delete(client: Any) -> None:
        agent_id = record["id"]
        pipe = client.pipeline(transaction=False)
        pipe.delete(_key("agent", agent_id))
        pipe.srem(_key("agents", "all"), agent_id)
        pipe.zrem(_key("agents", "registered"), agent_id)
        if record.get("status"):
            pipe.srem(_key("agents", "status", record["status"]), agent_id)
        for tag in record.get("tags") or []:
            pipe.srem(_key("tag", _key_fragment(tag), "agents"), agent_id)
        pipe.xadd(
            _key("events"),
            {
                "type": "agent.deleted",
                "id": agent_id,
                "at": _now_iso(),
            },
        )
        pipe.execute()

    _run(delete, strict=os.getenv("AGENT_REGISTRY_REDIS_STRICT") == "1")


def _conversation_key(conversation_id: str) -> str:
    return _key("conversation", conversation_id)


def _conversation_turns_key(conversation_id: str) -> str:
    return _key("conversation", conversation_id, "turns")


def _conversation_turn_index_key(conversation_id: str) -> str:
    return _key("conversation", conversation_id, "turn-index")


def _status_conversations_key(status: str) -> str:
    return _key("conversations", "status", _key_fragment(status))


def _conversation_hash(conversation: dict[str, Any]) -> dict[str, str]:
    return {
        "id": conversation["id"],
        "agent_id": conversation["agent_id"],
        "agent_name": conversation.get("agent_name") or "",
        "endpoint": conversation["endpoint"],
        "card_url": conversation.get("card_url") or "",
        "a2a_context_id": conversation.get("a2a_context_id") or "",
        "task_id": conversation.get("task_id") or "",
        "weave_trace_id": conversation.get("weave_trace_id") or "",
        "weave_root_call_id": conversation.get("weave_root_call_id") or "",
        "status": conversation.get("status") or "active",
        "created_at": conversation["created_at"],
        "updated_at": conversation["updated_at"],
    }


def _conversation_from_hash(
    row: dict[str, str],
    *,
    turns: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not row.get("id"):
        return None
    conversation: dict[str, Any] = {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "agent_name": row.get("agent_name") or None,
        "endpoint": row["endpoint"],
        "card_url": row.get("card_url") or None,
        "a2a_context_id": row.get("a2a_context_id") or None,
        "task_id": row.get("task_id") or None,
        "weave_trace_id": row.get("weave_trace_id") or None,
        "weave_root_call_id": row.get("weave_root_call_id") or None,
        "status": row.get("status") or "active",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if turns is not None:
        conversation["turns"] = turns
    return conversation


def _turn_payload(turn: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    payload = {
        "id": turn["id"],
        "conversation_id": turn["conversation_id"],
        "turn_index": turn["turn_index"],
        "request": turn["request"],
        "response": turn.get("response"),
        "error": turn.get("error"),
        "a2a_context_id": turn.get("a2a_context_id"),
        "task_id": turn.get("task_id"),
        "created_at": turn["created_at"],
    }
    if include_raw:
        payload["raw_response"] = turn.get("raw_response")
    return payload


def _list_turns(
    client: Any,
    conversation_id: str,
    *,
    include_raw: bool = False,
) -> list[dict[str, Any]]:
    rows = client.lrange(_conversation_turns_key(conversation_id), 0, -1)
    turns: list[dict[str, Any]] = []
    for row in rows:
        try:
            turn = json.loads(row)
        except ValueError:
            continue
        turns.append(_turn_payload(turn, include_raw=include_raw))
    return turns


def _expire_session_keys(pipe: Any, conversation_id: str) -> None:
    ttl = _session_ttl()
    if not ttl:
        return
    pipe.expire(_conversation_key(conversation_id), ttl)
    pipe.expire(_conversation_turns_key(conversation_id), ttl)
    pipe.expire(_conversation_turn_index_key(conversation_id), ttl)


def _sync_conversation(
    client: Any,
    conversation: dict[str, Any],
    *,
    event_type: str | None,
) -> None:
    conversation_id = conversation["id"]
    status = conversation.get("status") or "active"
    previous_status = client.hget(_conversation_key(conversation_id), "status")
    score = _timestamp(conversation.get("updated_at"))

    pipe = client.pipeline(transaction=False)
    pipe.hset(_conversation_key(conversation_id), mapping=_conversation_hash(conversation))
    pipe.zadd(_key("conversations", "all"), {conversation_id: score})
    pipe.zadd(
        _key("agent", conversation["agent_id"], "conversations"),
        {conversation_id: score},
    )
    if previous_status and previous_status != status:
        pipe.zrem(_status_conversations_key(previous_status), conversation_id)
    pipe.zadd(_status_conversations_key(status), {conversation_id: score})
    if event_type:
        pipe.xadd(
            _key("events"),
            {
                "type": event_type,
                "id": conversation_id,
                "agent_id": conversation["agent_id"],
                "at": _now_iso(),
            },
        )
    _expire_session_keys(pipe, conversation_id)
    pipe.execute()


def create_conversation(
    *,
    agent_id: str,
    agent_name: str | None,
    endpoint: str,
    card_url: str | None,
    weave_trace_id: str | None = None,
    weave_root_call_id: str | None = None,
) -> dict[str, Any]:
    client = _require_client()
    timestamp = _now_iso()
    conversation = {
        "id": f"conv_{uuid.uuid4().hex[:12]}",
        "agent_id": agent_id,
        "agent_name": agent_name,
        "endpoint": endpoint,
        "card_url": card_url,
        "a2a_context_id": None,
        "task_id": None,
        "weave_trace_id": weave_trace_id,
        "weave_root_call_id": weave_root_call_id,
        "status": "active",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    _sync_conversation(client, conversation, event_type="conversation.created")
    return get_conversation(conversation["id"]) or conversation


def set_conversation_trace(
    conversation_id: str,
    *,
    weave_trace_id: str | None,
    weave_root_call_id: str | None,
) -> None:
    client = _require_client()
    conversation = get_conversation(conversation_id, include_turns=False)
    if conversation is None:
        return
    conversation["weave_trace_id"] = weave_trace_id
    conversation["weave_root_call_id"] = weave_root_call_id
    conversation["updated_at"] = _now_iso()
    _sync_conversation(client, conversation, event_type="conversation.trace_updated")


def update_conversation_context(
    conversation_id: str,
    *,
    a2a_context_id: str | None,
    task_id: str | None,
    status: str = "active",
) -> None:
    client = _require_client()
    conversation = get_conversation(conversation_id, include_turns=False)
    if conversation is None:
        raise ValueError(f"No conversation with id {conversation_id!r}")
    conversation["a2a_context_id"] = a2a_context_id or conversation.get("a2a_context_id")
    conversation["task_id"] = task_id or conversation.get("task_id")
    conversation["status"] = status
    conversation["updated_at"] = _now_iso()
    _sync_conversation(client, conversation, event_type="conversation.updated")


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
    client = _require_client()
    turn_index = int(client.incr(_conversation_turn_index_key(conversation_id)))
    turn = {
        "id": f"turn_{uuid.uuid4().hex[:12]}",
        "conversation_id": conversation_id,
        "turn_index": turn_index,
        "request": request,
        "response": response,
        "raw_response": raw_response,
        "error": error,
        "a2a_context_id": a2a_context_id,
        "task_id": task_id,
        "created_at": _now_iso(),
    }
    pipe = client.pipeline(transaction=False)
    pipe.rpush(_conversation_turns_key(conversation_id), json.dumps(turn))
    pipe.xadd(
        _key("events"),
        {
            "type": "conversation.turn_appended",
            "id": conversation_id,
            "turn_id": turn["id"],
            "at": _now_iso(),
        },
    )
    _expire_session_keys(pipe, conversation_id)
    pipe.execute()
    return _turn_payload(turn)


def get_conversation(
    conversation_id: str,
    *,
    include_turns: bool = True,
    include_raw: bool = False,
) -> dict[str, Any] | None:
    client = _require_client()
    row = client.hgetall(_conversation_key(conversation_id))
    if not row:
        return None
    turns = (
        _list_turns(client, conversation_id, include_raw=include_raw)
        if include_turns
        else None
    )
    return _conversation_from_hash(row, turns=turns)


def list_conversations(
    *,
    status: str | None = None,
    agent_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    client = _require_client()
    if agent_id:
        index_key = _key("agent", agent_id, "conversations")
    elif status:
        index_key = _status_conversations_key(status)
    else:
        index_key = _key("conversations", "all")

    ids = client.zrevrange(index_key, 0, -1)
    conversations: list[dict[str, Any]] = []
    stale_ids: list[str] = []
    for conversation_id in ids:
        conversation = get_conversation(conversation_id, include_turns=False)
        if conversation is None:
            stale_ids.append(conversation_id)
            continue
        if status and conversation["status"] != status:
            continue
        if agent_id and conversation["agent_id"] != agent_id:
            continue
        conversations.append(conversation)
        if len(conversations) >= limit:
            break
    if stale_ids:
        client.zrem(index_key, *stale_ids)
    return conversations


def finish_conversation(conversation_id: str, status: str = "completed") -> None:
    client = _require_client()
    conversation = get_conversation(conversation_id, include_turns=False)
    if conversation is None:
        return
    conversation["status"] = status
    conversation["updated_at"] = _now_iso()
    _sync_conversation(client, conversation, event_type="conversation.finished")
