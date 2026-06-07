"""Agent Card fetching and validation.

Anti-spoofing measure: the registry always fetches the card from the
claimed domain itself rather than accepting a pasted card — registering
an agent proves control of the endpoint that serves its card.

Supports both A2A v1.0 cards (endpoint in supportedInterfaces[].url) and
v0.x cards (top-level url field).
"""

from __future__ import annotations

from typing import Any

import httpx

# Spec-current path first, legacy path second.
WELL_KNOWN_PATHS = (
    "/.well-known/agent-card.json",
    "/.well-known/agent.json",
)

FETCH_TIMEOUT = 10.0


async def fetch_agent_card(url: str) -> tuple[dict[str, Any], str]:
    """Fetch an agent card from a site base URL or a direct card URL.

    Returns (card, resolved_card_url). Raises ValueError if no candidate
    URL yields a JSON document.
    """
    if url.endswith(".json"):
        candidates = [url]
    else:
        base = url.rstrip("/")
        candidates = [base + path for path in WELL_KNOWN_PATHS]

    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
        for candidate in candidates:
            try:
                resp = await client.get(candidate)
                if resp.status_code == 200:
                    card = resp.json()
                    if isinstance(card, dict):
                        return card, candidate
            except (httpx.HTTPError, ValueError) as err:
                last_error = err
    raise ValueError(
        f"Could not fetch an agent card from {url} "
        f"(tried: {', '.join(candidates)})"
        + (f" — last error: {last_error}" if last_error else "")
    )


def validate_card(card: dict[str, Any]) -> list[str]:
    """Return a list of validation problems (empty list = valid)."""
    problems: list[str] = []
    for field in ("name", "description"):
        if not isinstance(card.get(field), str) or not card[field].strip():
            problems.append(f"card.{field} must be a non-empty string")
    skills = card.get("skills")
    if not isinstance(skills, list) or not skills:
        problems.append("card.skills must be a non-empty array")
    if extract_endpoint(card) is None:
        problems.append(
            "card must declare an A2A endpoint "
            "(supportedInterfaces[].url or top-level url)"
        )
    return problems


def extract_endpoint(card: dict[str, Any]) -> str | None:
    """A2A endpoint: v1.0 supportedInterfaces[].url, falling back to v0.x url."""
    for iface in card.get("supportedInterfaces") or []:
        if isinstance(iface, dict) and isinstance(iface.get("url"), str):
            return iface["url"]
    url = card.get("url")
    return url if isinstance(url, str) else None


def extract_tags(card: dict[str, Any]) -> list[str]:
    """Denormalize skill tags for filtering, lowercased and deduped."""
    tags: list[str] = []
    for skill in card.get("skills") or []:
        for tag in (skill.get("tags") or []) if isinstance(skill, dict) else []:
            if isinstance(tag, str):
                lowered = tag.lower()
                if lowered not in tags:
                    tags.append(lowered)
    return tags


def summarize(record: dict[str, Any], score: float | None = None) -> dict[str, Any]:
    """Compact view of a registry record for search/list results."""
    card = record["card"]
    summary: dict[str, Any] = {
        "id": record["id"],
        "name": card.get("name"),
        "description": card.get("description"),
        "endpoint": record["endpoint"],
        "status": record["status"],
        "tags": record["tags"],
        "skills": [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "description": s.get("description"),
            }
            for s in card.get("skills") or []
            if isinstance(s, dict)
        ],
        "last_seen_at": record["last_seen_at"],
    }
    if score is not None:
        summary["score"] = score
    return summary
