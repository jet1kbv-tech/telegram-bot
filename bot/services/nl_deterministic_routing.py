"""Small deterministic overrides for context questions with bounded wording."""
from __future__ import annotations

import re
import unicodedata

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


_NAMED_EVENT = re.compile(r"^(когда|во сколько|где)\s+(.+?)\s*[?!.]*$", re.IGNORECASE)


def named_event_question(text: str) -> ParsedIntent | None:
    """Route self-contained named event questions without provider date defaults."""
    match = _NAMED_EVENT.fullmatch(unicodedata.normalize("NFKC", text).strip())
    if match is None:
        return None
    question, target = match.groups()
    target = target.strip()
    normalized_target = _normalize_question(target)
    if (not target or normalized_target in {"обратно", "мы приезжаем", "приезжаем"}
            or normalized_target.startswith(("поезд ", "самолет ", "автобус "))):
        return None
    query_type = {"когда": "event_date", "во сколько": "event_time", "где": "event_place"}[
        question.casefold().replace("ё", "е")
    ]
    return ParsedIntent(IntentKind.QUERY_CONTEXT, {
        "query_type": query_type, "destination": None, "transport_type": None,
        "target": target, "date_expression": None, "person": None,
        "semantic_type": None, "follow_up": False,
    })
