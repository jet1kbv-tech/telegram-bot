from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from bot.services.nl_intent import IntentKind


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    item_id: str
    bucket: str
    item: dict[str, Any]


def normalize_for_match(text: str) -> str:
    """Return a deterministic comparison key without changing display text.

    Punctuation is intentionally preserved: entity resolution is exact after
    Unicode, case, Russian ``ё``/``е``, and whitespace normalization.
    """
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return " ".join(normalized.split())


# Backwards-compatible name used by read-only event query filtering.  Keeping
# one normalization primitive prevents query and mutation matching from
# drifting while leaving their existing equality/substring semantics intact.
normalize_reference = normalize_for_match


def resolve_entities(data: dict[str, Any], kind: IntentKind, target: str, *, owner: str = "",
                     include_past: bool = False, now: datetime | None = None,
                     timezone: str = "Europe/Moscow") -> list[EntityCandidate]:
    needle = normalize_for_match(target)
    candidates: list[EntityCandidate] = []
    if kind in {IntentKind.UPDATE_PURCHASE, IntentKind.DELETE_PURCHASE}:
        sources = [(bucket, data.get("purchases", {}).get(bucket, [])) for bucket in ("planned", "bought")]
        title_fields = ("title",)
    elif kind in {IntentKind.UPDATE_FILM, IntentKind.DELETE_FILM}:
        sources, title_fields = [("films", data.get("films", []))], ("title", "localized_title", "original_title")
    elif kind in {IntentKind.UPDATE_CALENDAR_EVENT, IntentKind.DELETE_CALENDAR_EVENT}:
        sources, title_fields = [(owner, data.get("calendars", {}).get(owner, []))], ("title",)
    else:
        sources, title_fields = [("afisha", data.get("afisha", []))], ("title",)
    for bucket, items in sources:
        for item in items if isinstance(items, list) else []:
            if kind in {IntentKind.UPDATE_CALENDAR_EVENT, IntentKind.DELETE_CALENDAR_EVENT} and item.get("source") != "manual":
                continue
            if kind in {IntentKind.UPDATE_CALENDAR_EVENT, IntentKind.DELETE_CALENDAR_EVENT} and not include_past:
                if _calendar_event_is_past(item, now=now, timezone=timezone):
                    continue
            if any(normalize_for_match(str(item.get(field) or "")) == needle for field in title_fields):
                candidates.append(EntityCandidate(str(item.get("id") or ""), bucket, dict(item)))
    if kind in {IntentKind.UPDATE_CALENDAR_EVENT, IntentKind.DELETE_CALENDAR_EVENT}:
        candidates.sort(key=lambda candidate: _calendar_sort_key(candidate.item))
    return candidates


def resolve_attachment_events(data: dict[str, Any], target: str, *, owner: str,
                              include_past: bool = False, now: datetime | None = None,
                              timezone: str = "Europe/Moscow") -> list[EntityCandidate]:
    """Resolve an attachment target across owned manual calendar and Afisha sources."""
    needle = normalize_for_match(target)
    candidates: list[EntityCandidate] = []
    for item in data.get("calendars", {}).get(owner, []):
        if item.get("source") != "manual" or normalize_for_match(str(item.get("title") or "")) != needle:
            continue
        if include_past or not _calendar_event_is_past(item, now=now, timezone=timezone):
            candidates.append(EntityCandidate(str(item.get("id") or ""), "calendar", dict(item)))
    for item in data.get("afisha", []):
        if item.get("status") == "active" and normalize_for_match(str(item.get("title") or "")) == needle:
            candidates.append(EntityCandidate(str(item.get("id") or ""), "afisha", dict(item)))
    candidates.sort(key=lambda candidate: _calendar_sort_key(candidate.item))
    return candidates


def upcoming_attachment_events(data: dict[str, Any], *, owner: str, now: datetime | None = None,
                               timezone: str = "Europe/Moscow", limit: int = 8) -> list[EntityCandidate]:
    """Return a small deterministic chooser without physical Afisha projections."""
    candidates = [
        EntityCandidate(str(item.get("id") or ""), "calendar", dict(item))
        for item in data.get("calendars", {}).get(owner, [])
        if item.get("source") == "manual" and not _calendar_event_is_past(item, now=now, timezone=timezone)
    ]
    candidates += [EntityCandidate(str(item.get("id") or ""), "afisha", dict(item))
                   for item in data.get("afisha", [])
                   if item.get("status") == "active"
                   and not _calendar_event_is_past(item, now=now, timezone=timezone)]
    candidates.sort(key=lambda candidate: _calendar_sort_key(candidate.item))
    return candidates[:max(1, limit)]


def _calendar_event_is_past(item: dict[str, Any], *, now: datetime | None, timezone: str) -> bool:
    local_now = now or datetime.now(ZoneInfo(timezone))
    if local_now.tzinfo is not None:
        local_now = local_now.astimezone(ZoneInfo(timezone)).replace(tzinfo=None)
    try:
        event_date = datetime.strptime(str(item.get("date") or ""), "%Y-%m-%d").date()
    except ValueError:
        return False  # Keep partial legacy records editable rather than hiding them.
    if event_date != local_now.date():
        return event_date < local_now.date()
    clock = item.get("end_time") or item.get("start_time") or item.get("time")
    if not clock:
        return False
    try:
        return datetime.strptime(str(clock), "%H:%M").time() < local_now.time()
    except ValueError:
        return False


def _calendar_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item.get("date") or "9999-12-31"), str(item.get("start_time") or "00:00")
