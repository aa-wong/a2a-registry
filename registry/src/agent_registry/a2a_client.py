"""Minimal A2A JSON-RPC client used by the router MCP server."""

from __future__ import annotations

import uuid
from typing import Any

import httpx


class A2AClientError(RuntimeError):
    """Raised when an A2A endpoint rejects or fails a message/send request."""


def _part_text(part: Any) -> str | None:
    if not isinstance(part, dict):
        return None
    text = part.get("text")
    if isinstance(text, str):
        return text
    root = part.get("root")
    if isinstance(root, dict) and isinstance(root.get("text"), str):
        return root["text"]
    return None


def _message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    texts = [
        text
        for part in message.get("parts") or []
        if (text := _part_text(part)) is not None
    ]
    return "\n".join(texts)


def _artifact_texts(artifacts: Any) -> list[str]:
    if not isinstance(artifacts, list):
        return []
    texts: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        artifact_text = _message_text(artifact)
        if artifact_text:
            texts.append(artifact_text)
    return texts


def _recursive_texts(value: Any) -> list[str]:
    if isinstance(value, dict):
        texts: list[str] = []
        direct_text = value.get("text")
        if isinstance(direct_text, str):
            texts.append(direct_text)
        for child in value.values():
            texts.extend(_recursive_texts(child))
        return texts
    if isinstance(value, list):
        texts = []
        for item in value:
            texts.extend(_recursive_texts(item))
        return texts
    return []


def extract_answer_text(result: Any) -> str:
    """Extract human-readable text from common A2A result shapes."""
    if not isinstance(result, dict):
        return ""

    artifact_text = "\n\n".join(_artifact_texts(result.get("artifacts")))
    if artifact_text:
        return artifact_text

    message_text = _message_text(result)
    if message_text:
        return message_text

    nested_message_text = _message_text(result.get("message"))
    if nested_message_text:
        return nested_message_text

    status = result.get("status")
    if isinstance(status, dict):
        status_text = _message_text(status.get("message"))
        if status_text:
            return status_text

    return "\n".join(dict.fromkeys(_recursive_texts(result)))


def extract_context_id(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    context_id = result.get("contextId") or result.get("context_id")
    if isinstance(context_id, str):
        return context_id
    for key in ("message", "task", "artifact_update", "status_update"):
        nested = result.get(key)
        if isinstance(nested, dict):
            context_id = nested.get("contextId") or nested.get("context_id")
            if isinstance(context_id, str):
                return context_id
    return None


def extract_task_id(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    task_id = result.get("taskId") or result.get("task_id")
    if isinstance(task_id, str):
        return task_id
    if result.get("kind") == "task" and isinstance(result.get("id"), str):
        return result["id"]
    for key in ("message", "task", "artifact_update", "status_update"):
        nested = result.get(key)
        if isinstance(nested, dict):
            task_id = nested.get("taskId") or nested.get("task_id") or nested.get("id")
            if isinstance(task_id, str):
                return task_id
    return None


async def send_message(
    *,
    endpoint: str,
    text: str,
    context_id: str | None = None,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Send one A2A message/send turn to an endpoint."""
    message: dict[str, Any] = {
        "role": "user",
        "messageId": str(uuid.uuid4()),
        "parts": [{"text": text}],
    }
    if context_id:
        message["contextId"] = context_id

    request_id = str(uuid.uuid4())
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            endpoint,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "message/send",
                "params": {"message": message},
            },
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise A2AClientError(
            f"A2A endpoint returned non-JSON response: HTTP {response.status_code}"
        ) from exc

    if response.status_code >= 400:
        raise A2AClientError(f"A2A HTTP {response.status_code}: {body}")
    if isinstance(body, dict) and body.get("error"):
        raise A2AClientError(f"A2A JSON-RPC error: {body['error']}")
    if not isinstance(body, dict) or "result" not in body:
        raise A2AClientError(f"A2A response missing result: {body}")

    result = body["result"]
    return {
        "jsonrpc_id": body.get("id", request_id),
        "answer": extract_answer_text(result),
        "context_id": extract_context_id(result) or context_id,
        "task_id": extract_task_id(result),
        "raw_result": result,
    }
