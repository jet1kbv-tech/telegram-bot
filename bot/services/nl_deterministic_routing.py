"""Small deterministic overrides for context questions with bounded wording."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any

from bot.services.context_queries import visible_event_match_count
from bot.services.nl_entity_resolution import resolve_entities
from bot.services.nl_intent import IntentKind, ParsedIntent


def _normalize_question(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    value = re.sub(r"[?!.,]+$", "", value.strip())
    return " ".join(value.split())


_FOLLOW_UPS = {
    "event": {
        "а где": "event_place", "где": "event_place",
        "а когда": "event_date", "когда": "event_date",
        "а во сколько": "event_time", "во сколько": "event_time",
        "а документы": "documents", "документы": "documents",
        "а билеты": "documents", "билеты": "documents",
    },
    "trip": {
        "а обратно": "return", "обратно": "return",
        "а когда обратно": "return", "когда обратно": "return",
        "а во сколько обратно": "return", "во сколько обратно": "return",
        "а во сколько приезжаем": "arrival", "во сколько приезжаем": "arrival",
        "а билеты": "documents", "билеты": "documents",
        "а документы": "documents", "документы": "documents",
        "а откуда": "origin", "откуда": "origin",
        "а куда": "destination", "куда": "destination",
        "а во сколько": "event_time", "во сколько": "event_time",
    },
}

_TYPED_EVENT_DOCUMENT_QUERY = re.compile(r"^события\s+со?\s+(.+)$")
_DOCUMENT_TYPES = {
    "бронью": "reservation",
    "бронь": "reservation",
    "бронированием": "reservation",
    "бронирование": "reservation",
    "билетом": "transport_ticket",
    "билетами": "transport_ticket",
    "билет": "transport_ticket",
    "страховкой": "insurance",
    "страховка": "insurance",
}


def typed_event_document_query(text: str) -> ParsedIntent | None:
    """Recognize only an explicit event query for one known document type."""
    match = _TYPED_EVENT_DOCUMENT_QUERY.fullmatch(_normalize_question(text))
    semantic_type = _DOCUMENT_TYPES.get(match.group(1)) if match else None
    if semantic_type is None:
        return None
    return ParsedIntent(IntentKind.QUERY_CONTEXT, {
        "query_type": "events_with_document_type", "destination": None,
        "transport_type": None, "target": None, "date_expression": None,
        "person": None, "semantic_type": semantic_type, "follow_up": False,
    })


def short_context_follow_up(text: str, domain: str) -> ParsedIntent | None:
    """Recognize only allow-listed phrases compatible with the active subject."""
    query_type = _FOLLOW_UPS.get(domain, {}).get(_normalize_question(text))
    if query_type is None:
        return None
    return ParsedIntent(IntentKind.QUERY_CONTEXT, {
        "query_type": query_type, "destination": None, "transport_type": None,
        "target": None, "date_expression": None, "person": None,
        "semantic_type": None, "follow_up": True,
    })


_DELETE_CALENDAR_EVENT = re.compile(r"^удали\s+(.+?)\s*$", re.IGNORECASE)


def delete_calendar_event_command(text: str, *, data: dict[str, Any], actor_key: str,
                                  now: datetime, timezone: str) -> ParsedIntent | None:
    """Route a bounded delete only when the existing resolver finds one event."""
    match = _DELETE_CALENDAR_EVENT.fullmatch(unicodedata.normalize("NFKC", text).strip())
    if match is None:
        return None
    target = match.group(1).strip()
    if not target:
        return None
    candidates = resolve_entities(
        data, IntentKind.DELETE_CALENDAR_EVENT, target, owner=actor_key,
        include_past=False, now=now, timezone=timezone,
    )
    if len(candidates) != 1:
        return None
    return ParsedIntent(IntentKind.DELETE_CALENDAR_EVENT, {
        "target": target, "date_expression": None,
    })


_NAMED_EVENT = re.compile(r"^(когда|во сколько|где)\s+(.+?)\s*[?!.]*$", re.IGNORECASE)


def named_event_question(text: str, *, data: dict[str, Any], actor_key: str,
                         now: datetime, timezone: str) -> ParsedIntent | None:
    """Route a named question only when its actor-visible event is unique."""
    match = _NAMED_EVENT.fullmatch(unicodedata.normalize("NFKC", text).strip())
    if match is None:
        return None
    question, target = match.groups()
    target = target.strip()
    normalized_target = _normalize_question(target)
    if (not target or normalized_target in {"обратно", "мы приезжаем", "приезжаем"}
            or normalized_target.startswith(("поезд ", "самолет ", "автобус "))):
        return None
    if visible_event_match_count(
            data, actor_key=actor_key, target=target, now=now, timezone=timezone) != 1:
        return None
    query_type = {"когда": "event_date", "во сколько": "event_time", "где": "event_place"}[
        question.casefold().replace("ё", "е")
    ]
    return ParsedIntent(IntentKind.QUERY_CONTEXT, {
        "query_type": query_type, "destination": None, "transport_type": None,
        "target": target, "date_expression": None, "person": None,
        "semantic_type": None, "follow_up": False,
    })
