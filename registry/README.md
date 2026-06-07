# Agent Registry

An MCP server that acts as an **active directory for A2A agents**: agents
register their [A2A Agent Cards](https://a2a-protocol.org), other agents
discover them via MCP tools, then communicate peer-to-peer over A2A.

> "npm for agents — register once, be discovered by every agent that speaks MCP."

Design doc: [`../docs/agent-registry-design.md`](../docs/agent-registry-design.md)

## Registry MCP tools

| Tool | Purpose |
|---|---|
| `register_agent(card_url)` | Register by site URL — the registry fetches `/.well-known/agent-card.json` (or `agent.json`) itself, which proves control of the domain |
| `search_agents(query, tags?, limit?)` | Ranked capability search over names, skills, tags, descriptions |
| `get_agent_card(agent_id)` | Full registry record incl. complete Agent Card + A2A endpoint |
| `list_agents(status?)` | Browse all registered agents |

Liveness: search/list lazily re-ping any agent not checked in the last
5 minutes; unreachable agents flip to `offline` and drop out of search.

## Setup

```bash
cd registry
uv sync
```

## One-shot install

The Python package exposes console scripts, so `uvx` can run it directly:

```bash
# From this checkout
uvx --from . agent-registry
uvx --from . agent-router

# From GitHub after pushing this repo
uvx --from "git+https://github.com/<owner>/a2a-registry.git#subdirectory=registry" agent-registry
```

This directory is also an npm package wrapper around those Python entry points.
The wrapper expects `uvx` to be installed and keeps the Python package as the
source of truth:

```bash
# From this checkout
npx --yes .

# After publishing the npm package
npx --yes @a2a-registry/agent-registry
npx --yes --package @a2a-registry/agent-registry agent-router
```

## Run

```bash
# stdio (local MCP clients)
uv run agent-registry

# Streamable HTTP (hosted — anyone can point their client at it)
uv run agent-registry --transport http --host 0.0.0.0 --port 8765

# React registration UI + lightweight JSON backend
uv run agent-registry-ui --port 8767
```

The web backend wraps the same SQLite DB and exposes:

```text
GET    /api/agents
POST   /api/agents        {"card_url": "https://example.com"}
DELETE /api/agents/{id}
```

The React app lives in `web/`. Build it for the backend to serve:

```bash
cd web
npm install
npm run build
```

For frontend development, run the backend on port 8767 and Vite separately:

```bash
cd web
npm run dev
```

## Router MCP server

`agent-router` is the MCP server for discovery plus A2A chat orchestration. It
uses the same SQLite registry, discovers up to 10 matching active agents by
default, asks them in parallel, and returns one `conversation_id` per agent.
If the first answer is not enough, the MCP client decides to call a continue
tool with the returned `conversation_id`; the router preserves that agent's
A2A `context_id`.

| Tool | Purpose |
|---|---|
| `search_agents(query, tags?, limit=10)` | Discover active agents by text search |
| `ask_agents(message, query?, tags?, limit=10, agent_ids?)` | Discover and ask selected agents once in parallel |
| `continue_agent_conversation(conversation_id, message)` | Follow up with one agent using its saved A2A context |
| `continue_agent_conversations(conversation_ids, message)` | Follow up with several prior conversations in parallel |
| `get_agent_conversation(conversation_id)` | Inspect the saved local transcript |
| `finish_agent_conversation(conversation_id)` | Optional: mark complete and close the Weave conversation trace |

```bash
# stdio (local MCP clients)
uv run agent-router

# Streamable HTTP
uv run agent-router --transport http --host 0.0.0.0 --port 8766
```

Weave tracing is required for the router. Set `AGENT_ROUTER_WEAVE_PROJECT` to
choose the project. Each router-managed A2A conversation gets a stable
`weave_trace_id`; follow-up turns are logged with the same `conversation_id` and
trace metadata rather than being treated as unrelated turns.

## Add to Claude Code

```bash
claude mcp add agent-registry -- uv --directory /path/to/weave-hack/registry run agent-registry

# Or, after publishing the npm wrapper:
claude mcp add agent-registry -- npx -y @a2a-registry/agent-registry
claude mcp add agent-router -- npx -y --package @a2a-registry/agent-registry agent-router
```

Then try: *"Find an agent that knows about Aaron and ask it about his AWS
experience"* — Claude will call `search_agents`, pull the card, and speak
A2A to the discovered endpoint with zero hardcoded knowledge.

## Smoke test

Runs the full demo loop against the live portfolio agent
(register → search → get card → two-turn A2A conversation):

```bash
uv run python tests/smoke_test.py
```

## Storage

SQLite defaults to `registry/registry.db` when run from a source checkout. When
run as an installed `uvx`/`npx` tool, it uses a per-user data directory. Override
either mode with `AGENT_REGISTRY_DB`.
Records store the provider's card verbatim plus registry metadata
(status, timestamps, denormalized tags). Router conversations and local turn
transcripts are stored in the same database.
