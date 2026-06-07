# Agent Registry — Design Document

> **Status:** Draft for review
> **Author:** Aaron Wong
> **Date:** 2026-06-06
> **Context:** Multi-agent hackathon project

---

## 1. Summary

A public **registry for AI agents** where:

1. Agent owners **register** their agents (via A2A Agent Cards).
2. Other agents **discover** them through an **MCP server** exposing search/lookup tools.
3. Once discovered, agents **communicate directly** with each other over the **A2A protocol**.

The registry is effectively **DNS + Yellow Pages for Agent Cards**: MCP is the query
interface, A2A is the collaboration channel. The two protocols are complementary by
design — MCP handles agent ↔ tool integration (vertical), A2A handles agent ↔ agent
collaboration (horizontal).

```
┌──────────────┐   1. register card    ┌────────────────────┐
│ Agent Owner  │ ────────────────────► │      Registry      │
└──────────────┘                       │  (DB + REST API)   │
                                       └─────────┬──────────┘
                                                 │ wraps
┌──────────────┐   2. search_agents()  ┌─────────▼──────────┐
│ Client Agent │ ────────────────────► │     MCP Server     │
│ (consumer)   │ ◄──────────────────── │  (discovery tools) │
└──────┬───────┘   matching cards      └────────────────────┘
       │
       │ 3. A2A protocol (tasks, messages, SSE streaming)
       ▼
┌──────────────┐
│ Target Agent │  (publicly reachable HTTP endpoint,
│ (provider)   │   serves /.well-known/agent-card.json)
└──────────────┘
```

---

## 2. Goals & Non-Goals

### Goals
- Working end-to-end demo: an agent discovers another agent it has **zero prior
  knowledge of** and completes a multi-turn task with it.
- Registration, semantic search, and card retrieval via MCP tools.
- Basic liveness tracking (delist dead agents).

### Non-Goals (hackathon scope)
- Production-grade trust/reputation system (acknowledged in §7, stubbed only).
- Billing, rate limiting, multi-tenancy.
- Relay/tunneling for agents behind NAT (noted as a future differentiator).
- Standardizing the registry protocol itself (we implement, not specify).

---

## 3. Components

| # | Component | Description | Effort |
|---|-----------|-------------|--------|
| 1 | **Registry API** | REST CRUD for Agent Cards + search. SQLite/Postgres. Optional: embeddings over skill descriptions for semantic search. | Small |
| 2 | **MCP Server** | Wraps the Registry API. Exposes `search_agents`, `get_agent_card`, `register_agent`, `list_agents`. Official MCP SDK (TS or Python), ~100–200 LOC. | Small |
| 3 | **Demo Agents (2–3)** | A2A-compliant agents with distinct skills (e.g., translator, summarizer, data-analyst). Built on `a2a-sdk` (Python) or `@a2a-js/sdk`. | Medium |
| 4 | **Orchestrator Agent** | The consumer-side demo: receives a task, queries the MCP registry, picks an agent, delegates over A2A, returns the result. | Medium |
| 5 | **Health Checker** | Background job pinging registered endpoints; marks agents stale/offline. | Small |

---

## 4. Data Model

### 4.1 Agent Card (stored verbatim from A2A spec)

We store the agent's own A2A Agent Card (normally served at
`/.well-known/agent-card.json`) plus registry-side metadata.

```jsonc
// A2A Agent Card (provider-authored)
{
  "name": "LegalDoc Translator",
  "description": "Translates legal documents between EN/ES/FR with terminology preservation.",
  "url": "https://agents.example.com/legal-translator",   // A2A endpoint
  "version": "1.2.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false
  },
  "authentication": {
    "schemes": ["bearer"]
  },
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain"],
  "skills": [
    {
      "id": "translate-legal",
      "name": "Legal translation",
      "description": "Translate contracts and filings, preserving defined terms.",
      "tags": ["translation", "legal", "en", "es", "fr"]
    }
  ]
}
```

### 4.2 Registry Record (registry-authored wrapper)

```jsonc
{
  "id": "agt_01HXYZ...",            // registry-assigned
  "card": { /* Agent Card above */ },
  "owner": "aaron@mosaia.io",        // registrant identity
  "registeredAt": "2026-06-06T18:00:00Z",
  "lastSeenAt": "2026-06-06T18:05:00Z",   // last successful health check
  "status": "active",                // active | stale | offline | delisted
  "verified": false,                 // domain-ownership proof passed (stretch)
  "embedding": [/* vector over name+description+skills, for semantic search */],
  "tags": ["translation", "legal"]   // denormalized from skills for filtering
}
```

---

## 5. MCP Tool Definitions

```jsonc
// Tool 1: search_agents — primary discovery path
{
  "name": "search_agents",
  "description": "Find agents in the registry matching a natural-language description of a needed capability.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query":  { "type": "string", "description": "What you need done, e.g. 'translate a Spanish contract to English'" },
      "tags":   { "type": "array", "items": { "type": "string" }, "description": "Optional tag filter" },
      "limit":  { "type": "integer", "default": 5 },
      "requireStreaming": { "type": "boolean", "default": false }
    },
    "required": ["query"]
  }
}
// Returns: ranked list of { id, name, description, skills[], url, status, score }

// Tool 2: get_agent_card — full card for a known agent
{
  "name": "get_agent_card",
  "inputSchema": {
    "type": "object",
    "properties": { "id": { "type": "string" } },
    "required": ["id"]
  }
}
// Returns: full Registry Record incl. complete Agent Card + auth schemes

// Tool 3: register_agent — self-service registration
{
  "name": "register_agent",
  "inputSchema": {
    "type": "object",
    "properties": {
      "cardUrl": { "type": "string", "description": "URL whose /.well-known/agent-card.json will be fetched and validated" }
    },
    "required": ["cardUrl"]
  }
}
// Registry fetches the card itself (anti-spoofing: card must be served from the
// claimed domain), validates against A2A schema, runs an initial health check.

// Tool 4: list_agents — browse/paginate (useful for demos and debugging)
{
  "name": "list_agents",
  "inputSchema": {
    "type": "object",
    "properties": {
      "status": { "type": "string", "enum": ["active", "stale", "offline"] },
      "cursor": { "type": "string" }
    }
  }
}
```

### Search implementation

- **MVP:** keyword match over `name + description + skill descriptions + tags`.
- **Flair:** embed those fields at registration time; embed the query at search
  time; cosine similarity rank. (Any embedding API; cache vectors in the DB.)

---

## 6. End-to-End Flow

```
 Orchestrator                MCP Registry              Target Agent
      │                           │                         │
      │ search_agents("translate │                         │
      │  legal contract ES→EN")  │                         │
      ├──────────────────────────►                         │
      │  [card: LegalDoc          │                         │
      │   Translator, score 0.91] │                         │
      ◄──────────────────────────┤                         │
      │                           │                         │
      │ get_agent_card(id)        │                         │
      ├──────────────────────────►                         │
      ◄──────────────────────────┤                         │
      │                           │                         │
      │        A2A: tasks/send (message + file part)        │
      ├────────────────────────────────────────────────────►
      │        A2A: SSE stream (status: working...)         │
      ◄────────────────────────────────────────────────────┤
      │        A2A: input-required? → tasks/send (answer)   │
      ├────────────────────────────────────────────────────►
      │        A2A: artifact (translated document)          │
      ◄────────────────────────────────────────────────────┤
```

Key demo beat: the orchestrator has **no hardcoded knowledge** of the target agent —
endpoint, auth scheme, and capabilities all come from the registry at runtime.

---

## 7. Known Challenges & Mitigations

| Challenge | Risk | Hackathon mitigation | Future direction |
|-----------|------|----------------------|------------------|
| **Trust & spoofing** — anyone can claim any capability | High | Fetch card from the claimed domain (proves endpoint control); manual curation of demo agents | Signed cards, domain verification (DNS TXT), reputation/ratings, usage-based scoring |
| **Capability semantics** — fuzzy matching of need → skill | Medium | Embeddings + tags | Lightweight capability taxonomy; structured I/O schemas per skill |
| **Reachability** — agents must be public HTTP servers | Medium | Deploy demo agents to a PaaS (Fly/Railway/Cloud Run); ngrok for local dev | Registry-operated relay for NAT'd agents — **potential differentiator** |
| **Auth handoff** — registry finds the agent, client still must auth | Medium | Open endpoints or shared API key for demo | Card declares schemes (per A2A spec); registry could broker OAuth flows |
| **Liveness/staleness** — cards drift from reality | Low | Health-check ping every N minutes; TTL + re-registration heartbeat | Webhook-based card-update notifications |
| **Result quality** — discovered agent may be bad at the task | Low (demo) | Curated demo agents | Ratings, test harnesses, "verified skill" badges |

---

## 8. Prior Art & Positioning

- **MCP Registry** (`registry.modelcontextprotocol.io`) — does this for MCP
  *servers*. We do the analogous thing for A2A *agents*. Validates the pattern.
- **A2A Agent Cards** — the spec's built-in discovery primitive
  (`/.well-known/agent-card.json`). The spec anticipates "registry-based
  discovery" as a deployment pattern but does not standardize the registry — that
  gap is exactly what this project fills.
- **A2A governance** — donated by Google to the Linux Foundation; registry and
  marketplace patterns are open discussion topics with no canonical
  implementation. A working demo is genuinely novel territory.

**Pitch line:** *"npm for agents — register once, be discovered by every agent that
speaks MCP, collaborate over A2A."*

---

## 9. Milestones

| Milestone | Deliverable | Target |
|-----------|-------------|--------|
| M1 | Registry API: register + list + keyword search, SQLite | Hour 0–4 |
| M2 | MCP server wrapping registry; verified from Claude/another MCP client | Hour 4–8 |
| M3 | Two demo A2A agents deployed and registered | Hour 8–14 |
| M4 | Orchestrator: discover → delegate → result, end to end | Hour 14–20 |
| M5 | Polish: semantic search, health checker, demo script | Hour 20–24 |

**Stretch goals (in priority order):**
1. Semantic search via embeddings
2. Domain-verified registration badge
3. Multi-agent fan-out (orchestrator splits a task across two discovered agents)
4. Simple web UI for browsing the registry

---

## 10. Tech Stack (proposed)

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Registry API | Node/TS + Fastify (or Python + FastAPI) | Speed of iteration |
| DB | SQLite (file) → Postgres if hosted | Zero-ops for hackathon |
| MCP server | Official `@modelcontextprotocol/sdk` (TS) or `mcp` (Python) | First-party, well-documented |
| A2A agents | `a2a-sdk` (Python) or `@a2a-js/sdk` | Official SDKs ship server scaffolding |
| Embeddings (stretch) | Any hosted embedding API | Cached at registration time |
| Hosting | Fly.io / Railway / Cloud Run; ngrok for local dev | Public HTTPS endpoints required by A2A |

---

## 11. Open Questions

1. **Registration identity** — email + API key, or GitHub OAuth? (MVP: API key.)
2. **Should the registry proxy A2A traffic** (observability, relay for NAT'd
   agents) or stay pure-discovery? (MVP: pure discovery; proxy is the v2 wedge.)
3. **Card refresh policy** — poll the well-known URL on a schedule, or require
   re-registration on version bump?
4. **MCP transport** — stdio for local demo vs. Streamable HTTP for a hosted
   registry anyone can point their client at. (Hosted HTTP is the better demo.)

---

## 12. Verdict

Architecturally sound, well-scoped for a hackathon, and positioned in a real gap
in the ecosystem. The strongest demo is the full loop: **agent A discovers agent B
via the MCP registry, then completes a multi-turn A2A task with it — with zero
prior knowledge of B's existence.**
