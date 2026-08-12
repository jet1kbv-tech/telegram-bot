"""Deterministic, Telegram-independent resolution of stored event attachments.

Route matching is deliberately conservative: NFKC/case/``ё`` normalization,
punctuation-to-spaces, whitespace collapse, then every query token must occur as
a complete token in the stored value.  There is no stemming, fuzzy search or AI.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from bot.services.event_attachments import resolve_attachment_parent

_PUNCTUATION = re.compile(r"[^\w]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class AttachmentMatch:
    attachment_id: str
    attachment: dict[str, Any]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AttachmentQueryResult:
    outcome: str
    candidates: tuple[AttachmentMatch, ...]
    total_count: int
    bounded: bool


def normalize_route(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold().replace("ё", "е")
    return tuple(_PUNCTUATION.sub(" ", normalized).split())


def route_contains(stored: str, query: str) -> bool:
    requested = normalize_route(query)
    available = set(normalize_route(stored))
    return bool(requested) and all(token in available for token in requested)


def _visible_parent(data: dict[str, Any], item: dict[str, Any], owner: str,
                    canonical_parent: tuple[str, str] | None) -> bool:
    try:
        parent_type, parent_id, parent = resolve_attachment_parent(
            data, str(item.get("parent_type") or ""), str(item.get("parent_event_id") or ""),
        )
    except ValueError:
        return False
    if canonical_parent and (parent_type, parent_id) != canonical_parent:
        return False
    if parent_type == "afisha":
        return parent.get("status") == "active"  # Existing shared product collection.
    return any(
        str(event.get("id") or "") == parent_id and event.get("source") == "manual"
        for event in data.get("calendars", {}).get(owner, [])
    )


def query_event_attachments(data: dict[str, Any], *, owner: str,
                            canonical_parent: tuple[str, str] | None = None,
                            semantic_type: str | None = None,
                            transport_type: str | None = None,
                            origin: str | None = None, destination: str | None = None,
                            date: str | None = None, person: str | None = None,
                            direction: str | None = None, limit: int = 10) -> AttachmentQueryResult:
    """Return reusable match records in stable storage order, bounded by ``limit``.

    Canonical enums, person, and departure ``date`` use exact normalized
    equality. ``direction`` is advisory: route metadata, never creation order,
    determines direction. With no filters all visible attachments are returned.
    """
    matches: list[AttachmentMatch] = []
    filters = (("semantic_type", semantic_type), ("transport_type", transport_type),
               ("date", date), ("person", person))
    for item in data.get("event_attachments", []):
        if not isinstance(item, dict) or not _visible_parent(data, item, owner, canonical_parent):
            continue
        reasons: list[str] = []
        failed = False
        for field, requested in filters:
            if requested is not None:
                if str(item.get(field) or "").strip().casefold() != str(requested).strip().casefold():
                    failed = True; break
                reasons.append(field)
        if failed:
            continue
        for field, requested in (("origin", origin), ("destination", destination)):
            if requested is not None:
                if not route_contains(str(item.get(field) or ""), requested):
                    failed = True; break
                reasons.append(field)
        if failed:
            continue
        if direction in {"outbound", "return"}:
            reasons.append("direction_advisory")
        matches.append(AttachmentMatch(str(item.get("id") or ""), dict(item), tuple(reasons)))
    bounded_limit = max(1, min(int(limit), 25))
    selected = tuple(matches[:bounded_limit])
    outcome = "none" if not matches else "single" if len(matches) == 1 else "multiple"
    return AttachmentQueryResult(outcome, selected, len(matches), len(matches) > bounded_limit)
