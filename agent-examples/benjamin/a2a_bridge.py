import os

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types.a2a_pb2 import Role, SendMessageRequest
from dotenv import load_dotenv


load_dotenv()


def _artifact_text(response: object) -> str | None:
    task = getattr(response, "task", None)
    if task is None:
        return None

    texts: list[str] = []
    for artifact in getattr(task, "artifacts", []):
        for part in getattr(artifact, "parts", []):
            text = getattr(part, "text", "")
            if text:
                texts.append(text)

    return "\n\n".join(texts) if texts else None


async def ask_a2a_agent(
    question: str,
    *,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
) -> str:
    base_url = base_url or os.getenv("A2A_BASE_URL", "http://127.0.0.1:9999")
    timeout_seconds = timeout_seconds or float(os.getenv("A2A_CLIENT_TIMEOUT", "180"))

    async with httpx.AsyncClient(timeout=timeout_seconds) as httpx_client:
        resolver = A2ACardResolver(
            httpx_client=httpx_client,
            base_url=base_url,
        )
        agent_card = await resolver.get_agent_card()

        client = await create_client(
            agent=agent_card,
            client_config=ClientConfig(
                streaming=False,
                httpx_client=httpx_client,
            ),
        )

        try:
            message = new_text_message(question, role=Role.ROLE_USER)
            request = SendMessageRequest(message=message)

            responses: list[str] = []
            async for response in client.send_message(request):
                responses.append(_artifact_text(response) or str(response))

            return "\n\n".join(responses) if responses else "No response from A2A agent."
        finally:
            await client.close()
