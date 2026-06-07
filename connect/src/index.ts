#!/usr/bin/env node
/**
 * a2a-connect — MCP server (stdio) for talking to A2A agents.
 *
 * Connect is per-user by design: it holds your private connections and
 * conversation handles (in ~/.a2a-connect/connect.db), so it runs locally
 * over stdio rather than as a shared hosted service.
 *
 *   claude mcp add a2a-connect -- npx -y a2a-connect
 */

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

import { buildServer } from "./server.js";

async function main(): Promise<void> {
  const server = buildServer();
  await server.connect(new StdioServerTransport());
}

main().catch((err) => {
  console.error("a2a-connect failed to start:", err);
  process.exit(1);
});
