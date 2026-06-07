"""Weave logging for router-managed A2A conversations."""

from __future__ import annotations

import os
import uuid
from typing import Any

from dotenv import load_dotenv

load_dotenv()


class WeaveConversationTracer:
    """Log A2A turns under a stable conversation trace.

    Each router conversation gets a stable trace/root call id. Follow-up MCP
    tool calls log new turn calls under that same trace and use the router
    conversation id as Weave's thread_id.
    """

    def __init__(self) -> None:
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        import weave

        project = (
            os.getenv("AGENT_ROUTER_WEAVE_PROJECT")
            or os.getenv("WEAVE_PROJECT")
            or "a2a-registry-router"
        )
        self._client = weave.init(project)
        return self._client

    def start_conversation(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        agent_name: str | None,
        endpoint: str,
        first_message: str,
    ) -> dict[str, str]:
        from weave.trace.context import call_context

        client = self._ensure_client()

        trace_id = uuid.uuid4().hex
        display_name = f"{agent_name or agent_id} conversation"
        inputs = {
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "endpoint": endpoint,
            "first_message": first_message,
        }
        attributes = {
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "a2a_endpoint": endpoint,
        }
        with call_context.set_thread_id(conversation_id):
            call = client.create_call(
                "a2a_conversation",
                inputs=inputs,
                attributes=attributes,
                display_name=display_name,
                use_stack=False,
                _call_id_override=trace_id,
            )

        return {"weave_trace_id": call.trace_id, "weave_root_call_id": call.id}

    def _parent_call(
        self,
        *,
        weave_trace_id: str,
        weave_root_call_id: str,
    ) -> Any:
        from weave.trace.call import Call

        client = self._ensure_client()
        return Call(
            _op_name="a2a_conversation",
            trace_id=weave_trace_id,
            project_id=client.project_id,
            parent_id=None,
            id=weave_root_call_id,
            inputs={},
        )

    def log_turn(
        self,
        *,
        conversation: dict[str, Any],
        turn_index: int,
        request: str,
        result: dict[str, Any] | None,
        error: str | None = None,
    ) -> None:
        from weave.trace.context import call_context

        client = self._ensure_client()
        trace_id = conversation.get("weave_trace_id")
        root_call_id = conversation.get("weave_root_call_id")
        if not trace_id or not root_call_id:
            raise ValueError(
                f"Conversation {conversation['id']!r} has no Weave trace ids"
            )

        parent = self._parent_call(
            weave_trace_id=trace_id,
            weave_root_call_id=root_call_id,
        )

        attributes = {
            "conversation_id": conversation["id"],
            "agent_id": conversation["agent_id"],
            "agent_name": conversation.get("agent_name"),
            "turn_index": turn_index,
        }
        inputs = {
            "conversation_id": conversation["id"],
            "agent_id": conversation["agent_id"],
            "endpoint": conversation["endpoint"],
            "context_id": conversation.get("a2a_context_id"),
            "message": request,
        }
        output = {
            "answer": (result or {}).get("answer"),
            "context_id": (result or {}).get("context_id"),
            "task_id": (result or {}).get("task_id"),
            "error": error,
        }
        exception = RuntimeError(error) if error else None

        with call_context.set_thread_id(conversation["id"]):
            call = client.create_call(
                "a2a_turn",
                inputs=inputs,
                parent=parent,
                attributes=attributes,
                display_name=f"turn {turn_index}: {conversation.get('agent_name') or conversation['agent_id']}",
                use_stack=False,
            )
            client.finish_call(call, output=output, exception=exception)

    def finish_conversation(
        self,
        *,
        conversation: dict[str, Any],
        output: dict[str, Any],
        error: str | None = None,
    ) -> None:
        client = self._ensure_client()
        trace_id = conversation.get("weave_trace_id")
        root_call_id = conversation.get("weave_root_call_id")
        if not trace_id or not root_call_id:
            raise ValueError(
                f"Conversation {conversation['id']!r} has no Weave trace ids"
            )

        root = self._parent_call(
            weave_trace_id=trace_id,
            weave_root_call_id=root_call_id,
        )

        client.finish_call(
            root,
            output=output,
            exception=RuntimeError(error) if error else None,
        )


tracer = WeaveConversationTracer()
