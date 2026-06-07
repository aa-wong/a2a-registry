/**
 * Consumer-side state: connections, conversations, and turns.
 *
 * This is the "delegation table" — the only state the consumer must keep.
 * A conversation row maps a local conv_ id to the remote agent's contextId;
 * the remote agent owns the actual conversational memory. Turns are also
 * logged locally for transcript visibility.
 *
 * Uses node:sqlite (built into Node >= 22.13) — no native deps, so npx
 * installs never need a compiler. DB lives in ~/.a2a-connect/ by default
 * (NOT relative to the package — npx runs from a cache dir).
 */

import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { DatabaseSync } from "node:sqlite";

import type { AgentCard } from "./cards.js";

const SCHEMA = `
CREATE TABLE IF NOT EXISTS connections (
    id           TEXT PRIMARY KEY,
    url          TEXT NOT NULL UNIQUE,
    card_url     TEXT NOT NULL,
    endpoint     TEXT NOT NULL,
    name         TEXT NOT NULL,
    card         TEXT NOT NULL,
    connected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id             TEXT PRIMARY KEY,
    connection_id  TEXT NOT NULL REFERENCES connections(id),
    context_id     TEXT,
    task_id        TEXT,
    state          TEXT,
    created_at     TEXT NOT NULL,
    last_active_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,
    text            TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
`;

export interface ConnectionRecord {
  id: string;
  url: string;
  card_url: string;
  endpoint: string;
  name: string;
  card: AgentCard;
  connected_at: string;
}

export interface ConversationRecord {
  id: string;
  connection_id: string;
  context_id: string | null;
  task_id: string | null;
  state: string | null;
  created_at: string;
  last_active_at: string;
}

export interface Turn {
  role: string;
  text: string;
  created_at: string;
}

function dbPath(): string {
  return (
    process.env.A2A_CONNECT_DB ?? join(homedir(), ".a2a-connect", "connect.db")
  );
}

let db: DatabaseSync | null = null;

function getDb(): DatabaseSync {
  if (!db) {
    const path = dbPath();
    mkdirSync(dirname(path), { recursive: true });
    db = new DatabaseSync(path);
    db.exec(SCHEMA);
  }
  return db;
}

function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

function toConnection(row: Record<string, unknown>): ConnectionRecord {
  return {
    id: row.id as string,
    url: row.url as string,
    card_url: row.card_url as string,
    endpoint: row.endpoint as string,
    name: row.name as string,
    card: JSON.parse(row.card as string) as AgentCard,
    connected_at: row.connected_at as string,
  };
}

// ── connections ──────────────────────────────────────────────────────────

export function upsertConnection(
  url: string,
  cardUrl: string,
  endpoint: string,
  name: string,
  card: AgentCard,
): ConnectionRecord {
  const d = getDb();
  const existing = d
    .prepare("SELECT id FROM connections WHERE url = ?")
    .get(url) as { id: string } | undefined;
  const connId = existing?.id ?? `conn_${randomUUID().replaceAll("-", "").slice(0, 12)}`;
  d.prepare(
    `INSERT INTO connections (id, url, card_url, endpoint, name, card, connected_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(url) DO UPDATE SET
         card_url = excluded.card_url,
         endpoint = excluded.endpoint,
         name = excluded.name,
         card = excluded.card`,
  ).run(connId, url, cardUrl, endpoint, name, JSON.stringify(card), nowIso());
  return getConnection(connId)!;
}

export function getConnection(connId: string): ConnectionRecord | null {
  const row = getDb()
    .prepare("SELECT * FROM connections WHERE id = ?")
    .get(connId) as Record<string, unknown> | undefined;
  return row ? toConnection(row) : null;
}

/** Resolve a connection by id, exact URL, or endpoint. */
export function findConnection(ref: string): ConnectionRecord | null {
  const row = getDb()
    .prepare("SELECT * FROM connections WHERE id = ? OR url = ? OR endpoint = ?")
    .get(ref, ref.replace(/\/+$/, ""), ref) as Record<string, unknown> | undefined;
  return row ? toConnection(row) : null;
}

export function listConnections(): ConnectionRecord[] {
  const rows = getDb()
    .prepare("SELECT * FROM connections ORDER BY connected_at")
    .all() as Record<string, unknown>[];
  return rows.map(toConnection);
}

// ── conversations & turns ────────────────────────────────────────────────

export function createConversation(connectionId: string): string {
  const convId = `conv_${randomUUID().replaceAll("-", "").slice(0, 12)}`;
  getDb()
    .prepare(
      `INSERT INTO conversations (id, connection_id, created_at, last_active_at)
       VALUES (?, ?, ?, ?)`,
    )
    .run(convId, connectionId, nowIso(), nowIso());
  return convId;
}

export function getConversation(convId: string): ConversationRecord | null {
  const row = getDb()
    .prepare("SELECT * FROM conversations WHERE id = ?")
    .get(convId) as ConversationRecord | undefined;
  return row ?? null;
}

export function listConversations(connectionId?: string): ConversationRecord[] {
  const d = getDb();
  const rows = connectionId
    ? d
        .prepare(
          "SELECT * FROM conversations WHERE connection_id = ? ORDER BY last_active_at DESC",
        )
        .all(connectionId)
    : d.prepare("SELECT * FROM conversations ORDER BY last_active_at DESC").all();
  return rows as unknown as ConversationRecord[];
}

export function updateConversation(
  convId: string,
  contextId: string | null,
  taskId: string | null,
  state: string | null,
): void {
  getDb()
    .prepare(
      `UPDATE conversations SET
           context_id = COALESCE(?, context_id),
           task_id = COALESCE(?, task_id),
           state = COALESCE(?, state),
           last_active_at = ?
       WHERE id = ?`,
    )
    .run(contextId, taskId, state, nowIso(), convId);
}

export function addTurn(convId: string, role: string, text: string): void {
  getDb()
    .prepare(
      "INSERT INTO turns (conversation_id, role, text, created_at) VALUES (?, ?, ?, ?)",
    )
    .run(convId, role, text, nowIso());
}

export function listTurns(convId: string): Turn[] {
  return getDb()
    .prepare(
      "SELECT role, text, created_at FROM turns WHERE conversation_id = ? ORDER BY id",
    )
    .all(convId) as unknown as Turn[];
}
