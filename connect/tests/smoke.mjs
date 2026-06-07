/**
 * End-to-end smoke test for a2a-connect (TypeScript build).
 *
 *   1. connectAgent        — fetch + store the live portfolio agent's card
 *   2. sendMessage         — new conversation, reply + conversation_id
 *   3. sendMessage (cont)  — same conversation_id; remote context carried
 *   4. delegation table    — listConnections / listConversations / transcript
 *   5. MCP transport       — real stdio client handshake + live tool call
 *
 * Run:  npm run smoke   (builds first, needs dist/)
 */

import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Isolated throwaway DB so the smoke test never pollutes real state.
const SMOKE_DB = join(mkdtempSync(join(tmpdir(), "a2a-connect-smoke-")), "connect.db");
process.env.A2A_CONNECT_DB = SMOKE_DB;

const ops = await import("../dist/ops.js");

const PORTFOLIO_URL = "https://aaronwongellis.com";

let failures = 0;
function check(label, ok, detail = "") {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures++;
}

console.log("\n[1/5] connectAgent");
const conn = await ops.connectAgent(PORTFOLIO_URL);
check(
  "connection established",
  conn.connection_id.startsWith("conn_"),
  `${conn.name} (${conn.connection_id})`,
);
check("endpoint resolved", Boolean(conn.endpoint), conn.endpoint);

console.log("\n[2/5] sendMessage — new conversation");
const r1 = await ops.sendMessage(conn.connection_id, "In one sentence, what does Aaron do?");
check("reply received", Boolean(r1.reply), r1.reply.slice(0, 90) + "...");
check("conversation created", r1.conversation_id.startsWith("conv_"), r1.conversation_id);

console.log("\n[3/5] sendMessage — continue conversation");
const r2 = await ops.sendMessage(
  conn.connection_id,
  "What was the last company you mentioned?",
  r1.conversation_id,
);
check(
  "context carried",
  Boolean(r2.reply) && r2.conversation_id === r1.conversation_id,
  r2.reply.slice(0, 90) + "...",
);

console.log("\n[4/5] delegation table");
check("connection listed", ops.listConnections().length === 1);
const convs = ops.listConversations();
check(
  "conversation listed",
  convs.length === 1 && convs[0].conversation_id === r1.conversation_id,
);
const transcript = ops.getConversation(r1.conversation_id);
check("transcript has 4 turns", transcript.turns.length === 4, `${transcript.turns.length} turns`);

console.log("\n[5/5] MCP stdio transport");
const { Client } = await import("@modelcontextprotocol/sdk/client/index.js");
const { StdioClientTransport } = await import("@modelcontextprotocol/sdk/client/stdio.js");

const client = new Client({ name: "smoke", version: "0.0.1" });
await client.connect(
  new StdioClientTransport({
    command: process.execPath,
    args: [new URL("../dist/index.js", import.meta.url).pathname],
    env: { ...process.env, A2A_CONNECT_DB: SMOKE_DB },
  }),
);
const tools = await client.listTools();
check(
  "5 tools listed",
  tools.tools.length === 5,
  tools.tools.map((t) => t.name).join(", "),
);
const result = await client.callTool({
  name: "send_message",
  arguments: { agent: PORTFOLIO_URL, message: "In five words or fewer, who is Aaron?" },
});
const parsed = JSON.parse(result.content[0].text);
check("tool call over MCP", Boolean(parsed.reply), parsed.reply);
check(
  "URL resolved to existing connection (no dupes)",
  ops.listConnections().length === 1,
);
await client.close();

console.log(`\n${failures === 0 ? "SMOKE TEST PASSED" : `${failures} CHECK(S) FAILED`}\n`);
process.exit(failures ? 1 : 0);
