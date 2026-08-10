from __future__ import annotations

import json
from typing import Any

from bot.services.nl_intent import IntentKind, IntentParserInvalidOutput, ParsedIntent

# This is the canonical provider/domain boundary.  The schema below is generated
# from the same definitions that the decoder uses, so the two cannot silently
# acquire different fields.
_FIELDS: dict[IntentKind, dict[str, tuple[type, ...]]] = {
    IntentKind.ADD_MOVIE_OR_TV: {"query": (str,)},
    IntentKind.ADD_PURCHASE: {
        "title": (str,), "price": (int, type(None)), "priority": (str, type(None)),
        "link": (str, type(None)), "comment": (str, type(None)), "buyer": (str, type(None)),
    },
    IntentKind.ADD_PERSONAL_CALENDAR_EVENT: {
        "title": (str,), "date_expression": (str, type(None)), "time_expression": (str, type(None)),
        "end_time_expression": (str, type(None)), "comment": (str, type(None)), "owner": (str,),
    },
    IntentKind.ADD_AFISHA_EVENT: {
        "title": (str,), "place": (str, type(None)), "date_expression": (str, type(None)),
        "time_expression": (str, type(None)), "end_date_expression": (str, type(None)),
        "end_time_expression": (str, type(None)), "link": (str, type(None)),
    },
    IntentKind.UPDATE_PURCHASE: {"target": (str,), "title": (str, type(None)), "price": (int, type(None)), "priority": (str, type(None)), "link": (str, type(None)), "comment": (str, type(None)), "buyer": (str, type(None)), "status": (str, type(None))},
    IntentKind.DELETE_PURCHASE: {"target": (str,)},
    IntentKind.UPDATE_FILM: {"target": (str,), "status": (str, type(None)), "comment": (str, type(None))},
    IntentKind.DELETE_FILM: {"target": (str,)},
    IntentKind.UPDATE_CALENDAR_EVENT: {"target": (str,), "title": (str, type(None)), "date_expression": (str, type(None)), "time_expression": (str, type(None))},
    IntentKind.DELETE_CALENDAR_EVENT: {"target": (str,)},
    IntentKind.UPDATE_AFISHA_EVENT: {"target": (str,), "title": (str, type(None)), "date_expression": (str, type(None)), "time_expression": (str, type(None))},
    IntentKind.DELETE_AFISHA_EVENT: {"target": (str,)},
    IntentKind.NO_ACTION: {},
    IntentKind.UNSUPPORTED: {"category": (str,)},
}

_OPTIONAL_NULL_FIELDS = {
    "price", "priority", "link", "comment", "buyer", "date_expression", "time_expression",
    "end_time_expression", "place", "end_date_expression", "title", "status",
}
_UNSUPPORTED = {"destructive", "other_user_calendar", "bulk", "unsupported_domain", "conversation"}


def decode_intent(raw: str | dict[str, Any]) -> ParsedIntent:
    """Strictly decode the canonical nested ``{intent, arguments}`` contract."""
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError) as exc:
        raise IntentParserInvalidOutput("invalid_json") from exc
    if not isinstance(value, dict) or set(value) != {"intent", "arguments"}:
        raise IntentParserInvalidOutput("invalid_envelope")
    if not isinstance(value["intent"], str) or not isinstance(value["arguments"], dict):
        raise IntentParserInvalidOutput("invalid_envelope")
    try:
        kind = IntentKind(value["intent"])
    except ValueError as exc:
        raise IntentParserInvalidOutput("unsupported_intent") from exc
    fields = _FIELDS[kind]
    supplied = value["arguments"]
    if set(supplied) != set(fields):
        raise IntentParserInvalidOutput("unexpected_fields")
    arguments: dict[str, Any] = {}
    for name, types in fields.items():
        item = supplied[name]
        # bool is an int subclass, but is never a valid monetary value.
        if isinstance(item, bool) or not isinstance(item, types):
            raise IntentParserInvalidOutput(f"invalid_{name}")
        if isinstance(item, str):
            item = item.strip()
            if len(item) > 1000:
                raise IntentParserInvalidOutput(f"oversized_{name}")
            if not item and name not in _OPTIONAL_NULL_FIELDS:
                raise IntentParserInvalidOutput(f"empty_{name}")
        arguments[name] = item
    if kind is IntentKind.ADD_PURCHASE:
        if arguments["price"] is not None and not 0 <= arguments["price"] <= 1_000_000_000:
            raise IntentParserInvalidOutput("invalid_price")
        if arguments["priority"] not in {None, "high", "medium", "low"}:
            raise IntentParserInvalidOutput("invalid_priority")
        if arguments["buyer"] not in {None, "current_user"}:
            raise IntentParserInvalidOutput("invalid_buyer")
    if kind is IntentKind.UPDATE_PURCHASE:
        if arguments["price"] is not None and not 0 <= arguments["price"] <= 1_000_000_000:
            raise IntentParserInvalidOutput("invalid_price")
        if arguments["priority"] not in {None, "high", "medium", "low", "none"}:
            raise IntentParserInvalidOutput("invalid_priority")
        if arguments["buyer"] not in {None, "current_user", "none"}:
            raise IntentParserInvalidOutput("invalid_buyer")
        if arguments["status"] not in {None, "planned", "bought"}:
            raise IntentParserInvalidOutput("invalid_status")
    if kind is IntentKind.UPDATE_FILM and arguments["status"] not in {None, "want", "watched"}:
        raise IntentParserInvalidOutput("invalid_status")
    if kind is IntentKind.ADD_PERSONAL_CALENDAR_EVENT and arguments["owner"] != "current_user":
        raise IntentParserInvalidOutput("invalid_owner")
    if kind is IntentKind.UNSUPPORTED and arguments["category"] not in _UNSUPPORTED:
        raise IntentParserInvalidOutput("invalid_category")
    return ParsedIntent(kind, arguments)


def _object_schema(kind: IntentKind, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["intent", "arguments"],
        "properties": {
            "intent": {"const": kind.value},
            "arguments": {
                "type": "object", "additionalProperties": False,
                "required": list(properties), "properties": properties,
            },
        },
    }


_BRANCH_PROPERTIES: dict[IntentKind, dict[str, Any]] = {
    IntentKind.ADD_MOVIE_OR_TV: {"query": {"type": "string"}},
    IntentKind.ADD_PURCHASE: {
        "title": {"type": "string"}, "price": {"type": ["integer", "null"], "minimum": 0, "maximum": 1_000_000_000},
        "priority": {"enum": ["high", "medium", "low", None]}, "link": {"type": ["string", "null"]},
        "comment": {"type": ["string", "null"]}, "buyer": {"enum": ["current_user", None]},
    },
    IntentKind.ADD_PERSONAL_CALENDAR_EVENT: {
        "title": {"type": "string"}, "date_expression": {"type": ["string", "null"]},
        "time_expression": {"type": ["string", "null"]}, "end_time_expression": {"type": ["string", "null"]},
        "comment": {"type": ["string", "null"]}, "owner": {"const": "current_user"},
    },
    IntentKind.ADD_AFISHA_EVENT: {
        "title": {"type": "string"}, "place": {"type": ["string", "null"]},
        "date_expression": {"type": ["string", "null"]}, "time_expression": {"type": ["string", "null"]},
        "end_date_expression": {"type": ["string", "null"]}, "end_time_expression": {"type": ["string", "null"]},
        "link": {"type": ["string", "null"]},
    },
    IntentKind.UPDATE_PURCHASE: {"target": {"type": "string"}, "title": {"type": ["string", "null"]}, "price": {"type": ["integer", "null"], "minimum": 0, "maximum": 1_000_000_000}, "priority": {"enum": ["high", "medium", "low", "none", None]}, "link": {"type": ["string", "null"]}, "comment": {"type": ["string", "null"]}, "buyer": {"enum": ["current_user", "none", None]}, "status": {"enum": ["planned", "bought", None]}},
    IntentKind.DELETE_PURCHASE: {"target": {"type": "string"}},
    IntentKind.UPDATE_FILM: {"target": {"type": "string"}, "status": {"enum": ["want", "watched", None]}, "comment": {"type": ["string", "null"]}},
    IntentKind.DELETE_FILM: {"target": {"type": "string"}},
    IntentKind.UPDATE_CALENDAR_EVENT: {"target": {"type": "string"}, "title": {"type": ["string", "null"]}, "date_expression": {"type": ["string", "null"]}, "time_expression": {"type": ["string", "null"]}},
    IntentKind.DELETE_CALENDAR_EVENT: {"target": {"type": "string"}},
    IntentKind.UPDATE_AFISHA_EVENT: {"target": {"type": "string"}, "title": {"type": ["string", "null"]}, "date_expression": {"type": ["string", "null"]}, "time_expression": {"type": ["string", "null"]}},
    IntentKind.DELETE_AFISHA_EVENT: {"target": {"type": "string"}},
    IntentKind.NO_ACTION: {},
    IntentKind.UNSUPPORTED: {"category": {"enum": sorted(_UNSUPPORTED)}},
}

INTENT_JSON_SCHEMA: dict[str, Any] = {
    "name": "telegram_bot_intent",
    "strict": True,
    "schema": {"oneOf": [_object_schema(kind, _BRANCH_PROPERTIES[kind]) for kind in IntentKind]},
}


def decode_provider_envelope(raw: str) -> ParsedIntent:
    return decode_intent(raw)
