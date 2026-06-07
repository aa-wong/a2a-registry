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

## Run

```bash
# stdio (local MCP clients)
uv run agent-registry

# Streamable HTTP (hosted — anyone can point their client at it)
uv run agent-registry --transport http --host 0.0.0.0 --port 8765
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

SQLite at `registry/registry.db` (override with `AGENT_REGISTRY_DB`).
Records store the provider's card verbatim plus registry metadata
(status, timestamps, denormalized tags). Router conversations and local turn
transcripts are stored in the same database.
