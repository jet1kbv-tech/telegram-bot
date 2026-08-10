from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from bot.services.nl_intent import IntentKind


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    item_id: str
    bucket: str
    item: dict[str, Any]


def normalize_reference(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().casefold().replace("ё", "е")
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def resolve_entities(data: dict[str, Any], kind: IntentKind, target: str, *, owner: str = "") -> list[EntityCandidate]:
    needle = normalize_reference(target)
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
            if any(normalize_reference(str(item.get(field) or "")) == needle for field in title_fields):
                candidates.append(EntityCandidate(str(item.get("id") or ""), bucket, dict(item)))
    return candidates
