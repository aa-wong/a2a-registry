/**
 * Thin A2A client: JSON-RPC message/send over HTTP.
 *
 * The remote may reply with either a Message (simple agents) or a Task
 * (task-lifecycle agents); extractReply normalizes both into a flat shape.
 */

import { randomUUID } from "node:crypto";

const SEND_TIMEOUT_MS = 60_000;

export class A2AError extends Error {
  constructor(
    public code: number,
    message: string,
    public data?: unknown,
  ) {
    super(`A2A error ${code}: ${message}`);
  }
}

export interface A2AReply {
  text: string;
  contextId: string | null;
  taskId: string | null;
  state: string | null;
}

interface Part {
  text?: unknown;
  [key: string]: unknown;
}

function textParts(parts: unknown): string {
  if (!Array.isArray(parts)) return "";
  return parts
    .filter((p): p is Part => !!p && typeof p === "object")
    .map((p) => p.text)
    .filter((t): t is string => typeof t === "string")
    .join("\n")
    .trim();
}

/** Normalize a Message or Task result into a flat reply. */
export function extractReply(result: Record<string, unknown>): A2AReply {
  const reply: A2AReply = {
    text: "",
    contextId: typeof result.contextId === "string" ? result.contextId : null,
    taskId: null,
    state: null,
  };
  if ("parts" in result) {
    // Message
    reply.text = textParts(result.parts);
  } else if ("status" in result) {
    // Task
    reply.taskId = typeof result.id === "string" ? result.id : null;
    const status = (result.status ?? {}) as Record<string, unknown>;
    reply.state = typeof status.state === "string" ? status.state : null;
    const chunks: string[] = [];
    if (status.message && typeof status.message === "object") {
      chunks.push(textParts((status.message as Record<string, unknown>).parts));
    }
    for (const artifact of Array.isArray(result.artifacts) ? result.artifacts : []) {
      if (artifact && typeof artifact === "object") {
        chunks.push(textParts((artifact as Record<string, unknown>).parts));
      }
    }
    reply.text = chunks.filter(Boolean).join("\n");
  }
  return reply;
}

/** Send one user message; returns the normalized reply. */
export async function sendA2AMessage(
  endpoint: string,
  text: string,
  contextId?: string | null,
): Promise<A2AReply> {
  const message: Record<string, unknown> = {
    role: "user",
    messageId: randomUUID(),
    parts: [{ text }],
  };
  if (contextId) message.contextId = contextId;

  const resp = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: randomUUID(),
      method: "message/send",
      params: { message },
    }),
    signal: AbortSignal.timeout(SEND_TIMEOUT_MS),
  });
  if (!resp.ok) {
    throw new Error(`A2A endpoint returned HTTP ${resp.status}`);
  }
  const body = (await resp.json()) as {
    result?: Record<string, unknown>;
    error?: { code?: number; message?: string; data?: unknown };
  };
  if (body.error) {
    throw new A2AError(
      body.error.code ?? -1,
      body.error.message ?? "unknown",
      body.error.data,
    );
  }
  return extractReply(body.result ?? {});
}
