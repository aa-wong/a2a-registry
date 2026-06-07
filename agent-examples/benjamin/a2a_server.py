import os
import re
from pathlib import Path

import uvicorn
import weave
from a2a.helpers import (
    get_message_text,
    new_task_from_user_message,
    new_text_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.types.a2a_pb2 import TaskState
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT
from agents import Agent, OpenAIChatCompletionsModel, RunConfig, Runner, function_tool
from dotenv import load_dotenv
from openai import AsyncOpenAI
from starlette.applications import Starlette
from weave.integrations.patch import patch_openai_agents

load_dotenv()

HOST = os.getenv("A2A_HOST", "127.0.0.1")
PORT = int(os.getenv("A2A_PORT", "9999"))
BASE_URL = os.getenv("A2A_BASE_URL", f"http://{HOST}:{PORT}")

VAULT_PATH = Path(os.getenv("OBSIDIAN_VAULT", "obsidian")).resolve()
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "z-ai/glm-5.1")
OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "http://localhost")
OPENROUTER_APP_NAME = os.getenv("OPENROUTER_APP_NAME", "smallagent")
WEAVE_PROJECT = os.getenv("WEAVE_PROJECT", "a2a-registry")

MAX_GREP_MATCHES = int(os.getenv("MAX_GREP_MATCHES", "25"))
MAX_GREP_LINE_CHARS = int(os.getenv("MAX_GREP_LINE_CHARS", "600"))
SEARCH_CONTEXT_LINES = int(os.getenv("SEARCH_CONTEXT_LINES", "2"))
SEARCH_STOP_WORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "as",
    "at",
    "for",
    "from",
    "in",
    "is",
    "me",
    "my",
    "of",
    "on",
    "or",
    "s",
    "the",
    "to",
    "what",
    "where",
    "with",
}
TEXT_EXTENSIONS = {
    ".canvas",
    ".csv",
    ".json",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
}

_weave_ready = False


def init_weave_once() -> None:
    global _weave_ready

    if _weave_ready:
        return

    weave.init(WEAVE_PROJECT)
    patch_openai_agents()
    _weave_ready = True


def _vault_files() -> list[Path]:
    files: list[Path] = []

    for path in VAULT_PATH.rglob("*"):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(VAULT_PATH).parts):
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        files.append(path)

    return sorted(files)


def _openrouter_model() -> OpenAIChatCompletionsModel:
    client = AsyncOpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=OPENROUTER_BASE_URL,
        default_headers={
            "HTTP-Referer": OPENROUTER_SITE_URL,
            "X-OpenRouter-Title": OPENROUTER_APP_NAME,
        },
    )
    return OpenAIChatCompletionsModel(
        model=OPENROUTER_MODEL,
        openai_client=client,
    )


def _search_terms(query: str) -> list[str]:
    terms = re.findall(r"[\w']+", query.casefold())
    return [term for term in terms if len(term) > 2 and term not in SEARCH_STOP_WORDS]


def _line_window(lines: list[str], index: int) -> tuple[int, int, str]:
    start = max(0, index - SEARCH_CONTEXT_LINES)
    end = min(len(lines), index + SEARCH_CONTEXT_LINES + 1)
    snippet = " | ".join(line.strip() for line in lines[start:end] if line.strip())
    if len(snippet) > MAX_GREP_LINE_CHARS:
        snippet = f"{snippet[:MAX_GREP_LINE_CHARS]}..."
    return start + 1, end, snippet


@weave.op(name="grep_obsidian_vault")
def _grep_obsidian_vault(query: str, max_matches: int = MAX_GREP_MATCHES) -> str:
    query = query.strip()
    max_matches = max(1, min(max_matches, 100))

    if not query:
        return "No grep query provided."
    if not VAULT_PATH.exists():
        return f"Vault path does not exist: {VAULT_PATH}"

    terms = _search_terms(query) or [query.casefold()]
    exact_query = query.casefold()
    matches: list[tuple[int, str, int, int, str]] = []

    for path in _vault_files():
        relative_path = path.relative_to(VAULT_PATH)
        path_text = str(relative_path).casefold()
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

        for index, line in enumerate(lines):
            start_line, end_line, snippet = _line_window(lines, index)
            content = snippet.casefold()
            hit_count = sum(1 for term in terms if term in content)
            if hit_count == 0:
                continue

            score = hit_count * 10
            if exact_query in content:
                score += 50
            score += sum(2 for term in terms if term in path_text)

            matches.append(
                (
                    score,
                    str(relative_path),
                    start_line,
                    end_line,
                    snippet,
                )
            )

    if not matches:
        return f"No content matches for: {query}"

    ranked_matches = sorted(set(matches), key=lambda item: (-item[0], item[1], item[2]))
    selected: list[tuple[str, int, int, str]] = []

    for _, relative_path, start_line, end_line, snippet in ranked_matches:
        overlaps_existing = any(
            relative_path == selected_path
            and start_line <= selected_end
            and end_line >= selected_start
            for selected_path, selected_start, selected_end, _ in selected
        )
        if overlaps_existing:
            continue

        selected.append((relative_path, start_line, end_line, snippet))
        if len(selected) >= max_matches:
            break

    return "\n".join(
        f"{relative_path}:{start_line}-{end_line}: {snippet}"
        for relative_path, start_line, end_line, snippet in selected
    )


@function_tool(name_override="search")
def search(query: str, max_matches: int = MAX_GREP_MATCHES) -> str:
    """Search note contents in Benjamin's local Obsidian vault.

    Args:
        query: Natural-language search text or keywords.
        max_matches: Maximum number of ranked matching snippets to return.
    """
    return _grep_obsidian_vault(query=query, max_matches=max_matches)


@function_tool(name_override="grep_vault")
def grep_vault(query: str, max_matches: int = MAX_GREP_MATCHES) -> str:
    """Search note contents in Benjamin's local Obsidian vault.

    Args:
        query: Natural-language search text or keywords.
        max_matches: Maximum number of ranked matching snippets to return.
    """
    return _grep_obsidian_vault(query=query, max_matches=max_matches)


@function_tool(name_override="grep_vauds")
def grep_vauds(query: str, max_matches: int = MAX_GREP_MATCHES) -> str:
    """Alias for search, kept because some models typo grep_vault."""
    return _grep_obsidian_vault(query=query, max_matches=max_matches)


@function_tool(name_override="grep_vaults")
def grep_vaults(query: str, max_matches: int = MAX_GREP_MATCHES) -> str:
    """Alias for search, kept because some models typo grep_vault."""
    return _grep_obsidian_vault(query=query, max_matches=max_matches)


obsidian_agent = Agent(
    name="Obsidian Knowledge Base Agent",
    model=_openrouter_model(),
    instructions=f"""
You answer questions about the Benjamin's knowledge. It's in his Obsidian vault.

Vault path: {VAULT_PATH}

Use the search tool to search for relevant notes before answering. The
tool returns matching lines as path:line: text. Base your answer on those
matches and say when the vault doesn't contain enough evidence. Use multiple
search calls with short keyword queries to find relevant notes.

Keep answers concise and useful, and don't mention the source documents.
""".strip(),
    tools=[search, grep_vault, grep_vauds, grep_vaults],
)


@weave.op(name="answer_obsidian_question")
async def answer_obsidian_question(question: str) -> str:
    init_weave_once()
    result = await Runner.run(
        starting_agent=obsidian_agent,
        input=question,
        max_turns=10,
        run_config=RunConfig(tool_not_found_behavior="return_error_to_model"),
    )
    return str(result.final_output)


class ObsidianKnowledgeExecutor(AgentExecutor):
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        if context.current_task:
            task = context.current_task
        else:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        task_updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task.id,
            context_id=task.context_id,
        )
        await task_updater.update_status(
            state=TaskState.TASK_STATE_WORKING,
            message=new_text_message("Searching the Obsidian vault..."),
        )

        question = get_message_text(context.message) or ""

        try:
            answer = await answer_obsidian_question(question)
        except Exception as exc:
            answer = f"{type(exc).__name__}: {exc}"
            final_state = TaskState.TASK_STATE_FAILED
        else:
            final_state = TaskState.TASK_STATE_COMPLETED

        await task_updater.add_artifact(
            parts=[new_text_part(text=answer, media_type="text/plain")],
        )
        final_message = (
            "Done" if final_state == TaskState.TASK_STATE_COMPLETED else "Failed"
        )
        await task_updater.update_status(
            state=final_state,
            message=new_text_message(final_message),
        )

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        raise NotImplementedError("Cancel is not supported.")


skill = AgentSkill(
    id="ask_obsidian_vault",
    name="Ask Obsidian vault",
    description="Answers questions about the local Obsidian knowledge base.",
    input_modes=["text/plain"],
    output_modes=["text/plain"],
    tags=["obsidian", "knowledge-base", "notes", "search"],
    examples=[
        "What do my notes say about Redis memory?",
        "Find notes related to agent protocols.",
    ],
)

agent_card = AgentCard(
    name="Obsidian Knowledge Base Agent",
    description="A2A agent that answers questions about the local ./obsidian vault.",
    version="0.1.0",
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    capabilities=AgentCapabilities(streaming=False),
    supported_interfaces=[
        AgentInterface(
            protocol_binding="JSONRPC",
            protocol_version=PROTOCOL_VERSION_CURRENT,
            url=BASE_URL,
        ),
    ],
    skills=[skill],
)

task_store = InMemoryTaskStore()

request_handler = DefaultRequestHandler(
    agent_executor=ObsidianKnowledgeExecutor(),
    task_store=task_store,
    agent_card=agent_card,
)

routes = []
routes.extend(create_agent_card_routes(agent_card))
routes.extend(create_jsonrpc_routes(request_handler, "/", enable_v0_3_compat=True))

app = Starlette(routes=routes)


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
