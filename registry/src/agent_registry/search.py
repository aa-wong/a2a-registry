"""Keyword search over registry records.

MVP ranking: tokenize the query, score token hits against card fields
with simple field weights. Swap this module for an embedding-based
ranker later without touching the server layer.
"""

from __future__ import annotations

import re
from typing import Any

# Field weights: tags are the most intentional signal, then name, then
# skill names, then free-text descriptions/examples.
WEIGHT_TAG = 4
WEIGHT_NAME = 3
WEIGHT_SKILL_NAME = 2
WEIGHT_TEXT = 1

STOPWORDS = {
    "a", "an", "and", "are", "can", "do", "does", "for", "how", "i", "is",
    "me", "of", "or", "that", "the", "to", "what", "which", "who", "with",
    "you", "agent", "agents",
}


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]


def score_record(query_tokens: list[str], record: dict[str, Any]) -> int:
    card = record["card"]
    tag_set = set(record["tags"])
    name_tokens = set(tokenize(card.get("name") or ""))
    text_parts = [card.get("description") or ""]
    skill_name_tokens: set[str] = set()
    for skill in card.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        skill_name_tokens.update(tokenize(skill.get("name") or ""))
        text_parts.append(skill.get("description") or "")
        text_parts.extend(e for e in (skill.get("examples") or []) if isinstance(e, str))
    text_tokens = set(tokenize(" ".join(text_parts)))

    score = 0
    for token in query_tokens:
        if token in tag_set:
            score += WEIGHT_TAG
        if token in name_tokens:
            score += WEIGHT_NAME
        if token in skill_name_tokens:
            score += WEIGHT_SKILL_NAME
        if token in text_tokens:
            score += WEIGHT_TEXT
    return score


def search(
    records: list[dict[str, Any]],
    query: str,
    tags: list[str] | None = None,
    limit: int = 5,
) -> list[tuple[dict[str, Any], int]]:
    """Rank records against a query; returns (record, score) pairs, best first."""
    query_tokens = tokenize(query)
    required_tags = {t.lower() for t in tags or []}

    scored: list[tuple[dict[str, Any], int]] = []
    for record in records:
        if required_tags and not required_tags.issubset(set(record["tags"])):
            continue
        score = score_record(query_tokens, record)
        if score > 0 or not query_tokens:
            scored.append((record, score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]
