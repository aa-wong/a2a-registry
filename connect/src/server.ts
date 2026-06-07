/**
 * A2A Connect MCP server.
 *
 * Companion to the agent registry: discover an agent there, then use these
 * tools to establish a connection and hold conversations with it over A2A.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import * as ops from "./ops.js";

function json(value: unknown) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(value, null, 2) }],
  };
}

export function buildServer(): McpServer {
  const server = new McpServer(
    { name: "a2a-connect", version: "0.1.0" },
    {
      instructions:
        "Establish connections and converse with A2A agents. Typical flow: " +
        "find an agent (e.g. via the agent-registry MCP server), then " +
        "connect_agent(url) to fetch its card, then send_message to talk to " +
        "it. Omit conversation_id to start a new conversation; pass it back " +
        "to continue one — the remote agent resumes from its own state via " +
        "the A2A contextId, which this server tracks for you.",
    },
  );

  server.tool(
    "connect_agent",
    "Establish a connection to an A2A agent: fetch its Agent Card and store " +
      "the endpoint for messaging. Accepts a site URL (e.g. https://example.com), " +
      "a direct card URL ending in .json, or an A2A endpoint URL from a registry " +
      "result. Reconnecting to the same URL refreshes the stored card.",
    { url: z.string().describe("The agent's site, card, or endpoint URL") },
    async ({ url }) => json(await ops.connectAgent(url)),
  );

  server.tool(
    "send_message",
    "Send a message to a connected A2A agent and return its reply. Omit " +
      "conversation_id to start a new conversation; pass a prior one to " +
      "continue it — the remote agent resumes from its own server-side state. " +
      "Returns the reply text plus the conversation_id to use for follow-ups.",
    {
      agent: z
        .string()
        .describe("A connection_id, or an agent URL (auto-connects if new)"),
      message: z
        .string()
        .describe("The message text. Keep it focused — some agents cap message length."),
      conversation_id: z
        .string()
        .optional()
        .describe("Omit to start a new conversation; pass back to continue one"),
    },
    async ({ agent, message, conversation_id }) =>
      json(await ops.sendMessage(agent, message, conversation_id)),
  );

  server.tool(
    "list_connections",
    "List agents a connection has been established with.",
    {},
    async () => json(ops.listConnections()),
  );

  server.tool(
    "list_conversations",
    "List conversations (the delegation table), most recently active first.",
    {
      connection_id: z
        .string()
        .optional()
        .describe("Optionally restrict to one connected agent"),
    },
    async ({ connection_id }) => json(ops.listConversations(connection_id)),
  );

  server.tool(
    "get_conversation",
    "Fetch a conversation's full local transcript.",
    {
      conversation_id: z
        .string()
        .describe("From send_message or list_conversations"),
    },
    async ({ conversation_id }) => json(ops.getConversation(conversation_id)),
  );

  return server;
}
