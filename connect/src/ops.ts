/**
 * The five operations behind the MCP tools, as plain functions so they
 * can be exercised directly by tests and reused by other entry points.
 */

import { extractEndpoint, fetchAgentCard } from "./cards.js";
import { sendA2AMessage } from "./client.js";
import * as store from "./store.js";
import type { ConnectionRecord } from "./store.js";

export interface ConnectionSummary {
  connection_id: string;
  name: string;
  description?: string;
  endpoint: string;
  skills: { id?: string; name?: string; description?: string }[];
  streaming: boolean;
  connected_at: string;
}

function connectionSummary(record: ConnectionRecord): ConnectionSummary {
  return {
    connection_id: record.id,
    name: record.name,
    description: record.card.description,
    endpoint: record.endpoint,
    skills: (record.card.skills ?? []).map((s) => ({
      id: s.id,
      name: s.name,
      description: s.description,
    })),
    streaming: Boolean(record.card.capabilities?.streaming),
    connected_at: record.connected_at,
  };
}

async function establish(url: string): Promise<ConnectionRecord> {
  const { card, cardUrl } = await fetchAgentCard(url);
  const endpoint = extractEndpoint(card);
  if (!endpoint) {
    throw new Error(`Agent card at ${cardUrl} declares no A2A endpoint`);
  }
  return store.upsertConnection(
    url.replace(/\/+$/, ""),
    cardUrl,
    endpoint,
    card.name ?? url,
    card,
  );
}

/** Resolve an agent reference, auto-connecting to unseen URLs. */
async function resolveConnection(agent: string): Promise<ConnectionRecord> {
  const record = store.findConnection(agent);
  if (record) return record;
  if (agent.startsWith("http://") || agent.startsWith("https://")) {
    return establish(agent);
  }
  throw new Error(
    `Unknown agent '${agent}' — pass a connection_id from list_connections or an agent URL to connect to.`,
  );
}

export async function connectAgent(url: string): Promise<ConnectionSummary> {
  return connectionSummary(await establish(url));
}

export interface SendResult {
  conversation_id: string;
  agent: string;
  reply: string;
  task_state?: string;
  note?: string;
}

export async function sendMessage(
  agent: string,
  message: string,
  conversationId?: string,
): Promise<SendResult> {
  const record = await resolveConnection(agent);

  let contextId: string | null = null;
  if (conversationId) {
    const conv = store.getConversation(conversationId);
    if (!conv) throw new Error(`No conversation '${conversationId}'`);
    if (conv.connection_id !== record.id) {
      throw new Error(
        `Conversation '${conversationId}' belongs to a different agent`,
      );
    }
    contextId = conv.context_id;
  } else {
    conversationId = store.createConversation(record.id);
  }

  const reply = await sendA2AMessage(record.endpoint, message, contextId);

  store.addTurn(conversationId, "user", message);
  if (reply.text) store.addTurn(conversationId, "agent", reply.text);
  store.updateConversation(conversationId, reply.contextId, reply.taskId, reply.state);

  const result: SendResult = {
    conversation_id: conversationId,
    agent: record.name,
    reply: reply.text,
  };
  if (reply.state) {
    result.task_state = reply.state;
    if (reply.state === "input-required") {
      result.note =
        "The agent needs more input — reply with send_message using this conversation_id.";
    }
  }
  return result;
}

export function listConnections(): ConnectionSummary[] {
  return store.listConnections().map(connectionSummary);
}

export function listConversations(connectionId?: string) {
  const names = new Map(store.listConnections().map((r) => [r.id, r.name]));
  return store.listConversations(connectionId).map((c) => ({
    conversation_id: c.id,
    agent: names.get(c.connection_id) ?? c.connection_id,
    connection_id: c.connection_id,
    task_state: c.state,
    created_at: c.created_at,
    last_active_at: c.last_active_at,
  }));
}

export function getConversation(conversationId: string) {
  const conv = store.getConversation(conversationId);
  if (!conv) throw new Error(`No conversation '${conversationId}'`);
  const connection = store.getConnection(conv.connection_id);
  return {
    conversation_id: conv.id,
    agent: connection?.name ?? conv.connection_id,
    task_state: conv.state,
    created_at: conv.created_at,
    last_active_at: conv.last_active_at,
    turns: store.listTurns(conversationId),
  };
}
