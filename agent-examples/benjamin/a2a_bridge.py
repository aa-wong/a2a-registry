import os
from typing import Any

import httpx
from a2a.client import ClientConfig, create_client
from a2a.helpers import get_artifact_text, get_message_text, new_text_message
from a2a.types.a2a_pb2 import Role, SendMessageRequest, StreamResponse
from dotenv import load_dotenv


load_dotenv()

PROTOCOL_BINDINGS = ["JSONRPC", "HTTP+JSON"]


def _message_request(question: str, context_id: str | None) -> SendMessageRequest:
    message = new_text_message(
        question,
        role=Role.ROLE_USER,
        context_id=context_id,
    )
    return SendMessageRequest(message=message)


def _task_text(task: object) -> str:
    texts: list[str] = []
    for artifact in getattr(task, "artifacts", []):
        text = get_artifact_text(artifact)
        if text:
            texts.append(text)
    return "\n\n".join(texts)


def _response_answer_text(response: StreamResponse) -> str:
    if response.HasField("message"):
        return get_message_text(response.message)
    if response.HasField("task"):
        return _task_text(response.task)
    if response.HasField("artifact_update"):
        return get_artifact_text(response.artifact_update.artifact)
    return ""


def _response_status_text(response: StreamResponse) -> str:
    if not response.HasField("status_update"):
        return ""
    status = response.status_update.status
    if status.HasField("message"):
        return get_message_text(status.message)
    return ""


def _response_ids(response: StreamResponse) -> tuple[str | None, str | None]:
    if response.HasField("task"):
        return response.task.context_id or None, response.task.id or None
    if response.HasField("message"):
        return response.message.context_id or None, response.message.task_id or None
    if response.HasField("artifact_update"):
        event = response.artifact_update
        return event.context_id or None, event.task_id or None
    if response.HasField("status_update"):
        event = response.status_update
        return event.context_id or None, event.task_id or None
    return None, None


def _metadata_response(
    *,
    answer: str,
    context_id: str | None,
    task_id: str | None,
) -> dict[str, Any]:
    return {
        "answer": answer,
        "context_id": context_id,
        "task_id": task_id,
    }


async def ask_a2a_agent(
    question: str,
    *,
    context_id: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    return_metadata: bool = False,
) -> str | dict[str, str | None]:
    base_url = base_url or os.getenv("A2A_BASE_URL", "http://127.0.0.1:9999")
    timeout_seconds = timeout_seconds or float(os.getenv("A2A_CLIENT_TIMEOUT", "180"))

    async with httpx.AsyncClient(timeout=timeout_seconds) as httpx_client:
        client = await create_client(
            agent=base_url,
            client_config=ClientConfig(
                streaming=True,
                httpx_client=httpx_client,
                supported_protocol_bindings=PROTOCOL_BINDINGS,
            ),
        )

        try:
            answers: list[str] = []
            status_texts: list[str] = []
            response_context_id = context_id
            response_task_id: str | None = None

            async for response in client.send_message(
                _message_request(question, context_id)
            ):
                event_context_id, event_task_id = _response_ids(response)
                response_context_id = event_context_id or response_context_id
                response_task_id = event_task_id or response_task_id

                answer_text = _response_answer_text(response)
                if answer_text:
                    answers.append(answer_text)
                    continue

                status_text = _response_status_text(response)
                if status_text:
                    status_texts.append(status_text)

            answer = "\n\n".join(answers or status_texts)
            if not answer:
                answer = "No response from A2A agent."

            if return_metadata:
                return _metadata_response(
                    answer=answer,
                    context_id=response_context_id,
                    task_id=response_task_id,
                )
            return answer
        finally:
            await client.close()
