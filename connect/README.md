# A2A Connect

MCP server for **establishing connections and conversing with A2A agents** —
the communication half of the project. The [registry](../registry/) answers
*"who can do this?"*; this server answers *"talk to them."*

Built with the official TypeScript [`@modelcontextprotocol/sdk`](https://github.com/modelcontextprotocol/typescript-sdk)
and runnable with `npx` — connect is **per-user by design** (it holds your
private connections and conversation handles), so unlike the hosted registry,
everyone runs their own local instance.

It owns the consumer-side **delegation table**: every conversation maps a
local `conversation_id` to the remote agent's A2A `contextId`, persisted in
SQLite (via `node:sqlite` — zero native deps) at `~/.a2a-connect/connect.db`,
so conversations survive restarts. The remote agent keeps the actual
conversational memory — this server just keeps the handles, plus a local
transcript for visibility.

## MCP tools

| Tool | Purpose |
|---|---|
| `connect_agent(url)` | Fetch the Agent Card from a site/card/endpoint URL, store the connection |
| `send_message(agent, message, conversation_id?)` | A2A `message/send`. Omit `conversation_id` to start fresh; pass it to continue — `contextId` is handled for you. Auto-connects if `agent` is a new URL |
| `list_connections()` | Agents we've connected to |
| `list_conversations(connection_id?)` | The delegation table, most recent first |
| `get_conversation(conversation_id)` | Full local transcript |

Handles both A2A reply shapes: plain Messages and Tasks (artifacts +
lifecycle state — `input-required` is surfaced with a hint to reply).

## Install & run

Requires Node >= 22.13 (for the built-in `node:sqlite`).

```bash
# Once published to npm:
npx -y a2a-connect

# From this repo (local development):
cd connect
npm install
npm run build
node dist/index.js
```

## Add to Claude Code (alongside the registry)

```bash
# Published:
claude mcp add a2a-connect -- npx -y a2a-connect

# Local checkout:
claude mcp add a2a-connect -- node /path/to/weave-hack/connect/dist/index.js
```

Full demo loop: *"Find an agent that knows about Aaron and ask about his AWS
experience"* → `search_agents` (registry) → `connect_agent` → `send_message`
→ follow-ups continue the same conversation.

## Smoke test

```bash
npm run smoke
```

Connects to the live portfolio agent, holds a two-turn conversation proving
remote context carries, verifies the delegation table and transcript, then
re-runs a live tool call through a real MCP stdio client.

## Storage

SQLite at `~/.a2a-connect/connect.db` (override with `A2A_CONNECT_DB`):
`connections` (cards + endpoints), `conversations` (local id ↔ remote
`contextId`/`taskId`/state), `turns` (transcript).
