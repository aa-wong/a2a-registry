# Agent Registry — Active Directory for A2A Agents

> "npm for agents — register once, be discovered by every agent that speaks MCP,
> collaborate over A2A."

A multi-agent hackathon project: a **registry where AI agents register their
[A2A](https://a2a-protocol.org) Agent Cards**, other agents **discover them via
[MCP](https://modelcontextprotocol.io) tools**, and then **communicate
peer-to-peer over the A2A protocol** — with zero prior knowledge of each other.

```
┌──────────────┐   1. register card    ┌────────────────────┐
│ Agent Owner  │ ────────────────────► │   Agent Registry   │
└──────────────┘                       │ (MCP server + DB)  │
                                       └─────────┬──────────┘
┌──────────────┐   2. search_agents()            │
│ Client Agent │ ────────────────────────────────┘
│ (consumer)   │ ◄──────── matching Agent Cards
└──────┬───────┘
       │ 3. A2A protocol (JSON-RPC message/send, contextId, SSE)
       ▼
┌──────────────┐
│ Target Agent │  serves /.well-known/agent-card.json
└──────────────┘
```

## Why

MCP solves agent ↔ tool integration (vertical); A2A solves agent ↔ agent
collaboration (horizontal). A2A has a built-in discovery primitive — the Agent
Card at `/.well-known/agent-card.json` — and its spec anticipates
registry-based discovery, but no canonical registry exists. This project fills
that gap: the registry is **DNS + Yellow Pages for Agent Cards**, with MCP as
the query interface.

## Repository layout

| Path | Contents |
|---|---|
| [`registry/`](registry/) | The registry itself — a Python MCP server (FastMCP + SQLite) exposing `register_agent`, `search_agents`, `get_agent_card`, `list_agents` |
| [`docs/agent-registry-design.md`](docs/agent-registry-design.md) | Full design doc: architecture, data model, state management, trust challenges, milestones |

## Quick start

Run the registry directly from this checkout:

```bash
cd registry
uv sync

# Run the end-to-end smoke test (register → search → get card → live two-turn A2A conversation)
uv run python tests/smoke_test.py

# Run the MCP server (stdio)
uv run agent-registry

# Or hosted (Streamable HTTP)
uv run agent-registry --transport http --host 0.0.0.0 --port 8765
```

Or use one-shot install commands:

```bash
# Python-native
uvx --from ./registry agent-registry

# Node/MCP-client friendly wrapper
npx --yes ./registry
```

### Use it from Claude Code

```bash
claude mcp add agent-registry -- uv --directory "$(pwd)/registry" run agent-registry

# After publishing the npm wrapper:
claude mcp add agent-registry -- npx -y @a2a-registry/agent-registry
```

Then ask: *"Find an agent that knows about Aaron and ask it about his AWS
experience."* Claude discovers the agent via `search_agents`, pulls its card,
and speaks A2A directly to the discovered endpoint.

## How a conversation works

1. `search_agents("answer questions about Aaron's career")` → ranked Agent Card summaries
2. `get_agent_card(id)` → full card, including the A2A endpoint and auth schemes
3. `POST <endpoint>` with JSON-RPC `message/send` → reply includes a `contextId`
4. Echo the `contextId` on follow-up messages → the remote agent resumes the
   conversation from its own server-side state

The registry holds **no conversation state** — it is pure discovery.
Conversation state is owned by the remote agent (keyed by A2A `contextId`);
the consumer keeps only a small delegation table of handles. See
[design doc §6](docs/agent-registry-design.md) for details.

## Trust model (hackathon scope)

Registration requires the registry to fetch the card from the claimed domain
itself — registering `https://example.com` proves control of the server at
that domain. Liveness checks delist unreachable agents. Signed cards,
reputation, and domain verification badges are future work (design doc §7).

## Status

- [x] Design doc
- [x] Registry MCP server (4 tools, stdio + Streamable HTTP)
- [x] Lazy health checking (offline agents drop out of search)
- [x] Smoke test passing against a live A2A agent (aaronwongellis.com)
- [ ] Orchestrator agent (discover → delegate demo)
- [ ] Additional demo agents registered
- [ ] Semantic search via embeddings (stretch)
