/**
 * Agent Card discovery for outbound connections.
 *
 * Given a site URL, find the A2A Agent Card and the endpoint to talk to.
 * Supports A2A v1.0 cards (supportedInterfaces[].url) and v0.x (url).
 */

export interface AgentSkill {
  id?: string;
  name?: string;
  description?: string;
  tags?: string[];
}

export interface AgentCard {
  name?: string;
  description?: string;
  url?: string;
  supportedInterfaces?: { url?: string; protocolBinding?: string }[];
  capabilities?: { streaming?: boolean };
  skills?: AgentSkill[];
  [key: string]: unknown;
}

// Spec-current path first, legacy path second.
const WELL_KNOWN_PATHS = [
  "/.well-known/agent-card.json",
  "/.well-known/agent.json",
];

const FETCH_TIMEOUT_MS = 10_000;

/**
 * Fetch an agent card from a site base URL or a direct card URL.
 * Returns the card plus the URL it was resolved from.
 */
export async function fetchAgentCard(
  url: string,
): Promise<{ card: AgentCard; cardUrl: string }> {
  const candidates = url.endsWith(".json")
    ? [url]
    : WELL_KNOWN_PATHS.map((p) => url.replace(/\/+$/, "") + p);

  let lastError: unknown = null;
  for (const candidate of candidates) {
    try {
      const resp = await fetch(candidate, {
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
        redirect: "follow",
      });
      if (resp.ok) {
        const card = (await resp.json()) as unknown;
        if (card && typeof card === "object" && !Array.isArray(card)) {
          return { card: card as AgentCard, cardUrl: candidate };
        }
      }
    } catch (err) {
      lastError = err;
    }
  }
  throw new Error(
    `Could not fetch an agent card from ${url} (tried: ${candidates.join(", ")})` +
      (lastError ? ` — last error: ${String(lastError)}` : ""),
  );
}

/** A2A endpoint: v1.0 supportedInterfaces[].url, falling back to v0.x url. */
export function extractEndpoint(card: AgentCard): string | null {
  for (const iface of card.supportedInterfaces ?? []) {
    if (typeof iface?.url === "string") return iface.url;
  }
  return typeof card.url === "string" ? card.url : null;
}
