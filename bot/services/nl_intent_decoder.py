from __future__ import annotations

import json
from typing import Any

from bot.services.nl_intent import IntentKind, IntentParserInvalidOutput, ParsedIntent

_FIELDS: dict[IntentKind, dict[str, tuple[type, ...]]] = {
    IntentKind.ADD_MOVIE_OR_TV: {"query": (str,)},
    IntentKind.ADD_PURCHASE: {
        "title": (str,), "price_text": (str, type(None)), "priority": (str, type(None)),
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
    IntentKind.NO_ACTION: {},
    IntentKind.UNSUPPORTED: {"category": (str,)},
}

_OPTIONAL_NULL_FIELDS = {
    "price_text", "priority", "link", "comment", "buyer", "date_expression", "time_expression",
    "end_time_expression", "place", "end_date_expression",
}
_UNSUPPORTED = {"destructive", "other_user_calendar", "bulk", "unsupported_domain", "conversation"}


def decode_intent(raw: str | dict[str, Any]) -> ParsedIntent:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError) as exc:
        raise IntentParserInvalidOutput("invalid_json") from exc
    if not isinstance(value, dict) or not isinstance(value.get("intent"), str):
        raise IntentParserInvalidOutput("invalid_root")
    try:
        kind = IntentKind(value["intent"])
    except ValueError as exc:
        raise IntentParserInvalidOutput("unsupported_intent") from exc
    fields = _FIELDS[kind]
    expected = {"intent", *fields}
    if set(value) != expected:
        raise IntentParserInvalidOutput("unexpected_fields")
    arguments: dict[str, Any] = {}
    for name, types in fields.items():
        item = value[name]
        if not isinstance(item, types):
            raise IntentParserInvalidOutput(f"invalid_{name}")
        if isinstance(item, str):
            item = item.strip()
            if len(item) > 1000:
                raise IntentParserInvalidOutput(f"oversized_{name}")
            if not item and name not in _OPTIONAL_NULL_FIELDS:
                raise IntentParserInvalidOutput(f"empty_{name}")
        arguments[name] = item
    if kind is IntentKind.ADD_PURCHASE:
        if arguments["priority"] not in {None, "high", "medium", "low"}:
            raise IntentParserInvalidOutput("invalid_priority")
        if arguments["buyer"] not in {None, "current_user"}:
            raise IntentParserInvalidOutput("invalid_buyer")
    if kind is IntentKind.ADD_PERSONAL_CALENDAR_EVENT and arguments["owner"] != "current_user":
        raise IntentParserInvalidOutput("invalid_owner")
    if kind is IntentKind.UNSUPPORTED and arguments["category"] not in _UNSUPPORTED:
        raise IntentParserInvalidOutput("invalid_category")
    return ParsedIntent(kind, arguments)


INTENT_JSON_SCHEMA: dict[str, Any] = {
    "name": "telegram_bot_intent",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["intent", "arguments"],
        "properties": {
            "intent": {"enum": [kind.value for kind in IntentKind]},
            "arguments": {
                "oneOf": [
                    {"type": "object", "additionalProperties": False, "required": ["query"],
                     "properties": {"query": {"type": "string"}}},
                    {"type": "object", "additionalProperties": False,
                     "required": ["title", "price_text", "priority", "link", "comment", "buyer"],
                     "properties": {
                         "title": {"type": "string"}, "price_text": {"type": ["string", "null"]},
                         "priority": {"enum": ["high", "medium", "low", None]},
                         "link": {"type": ["string", "null"]}, "comment": {"type": ["string", "null"]},
                         "buyer": {"enum": ["current_user", None]},
                     }},
                    {"type": "object", "additionalProperties": False,
                     "required": ["title", "date_expression", "time_expression", "end_time_expression", "comment", "owner"],
                     "properties": {
                         "title": {"type": "string"}, "date_expression": {"type": ["string", "null"]},
                         "time_expression": {"type": ["string", "null"]},
                         "end_time_expression": {"type": ["string", "null"]},
                         "comment": {"type": ["string", "null"]}, "owner": {"const": "current_user"},
                     }},
                    {"type": "object", "additionalProperties": False,
                     "required": ["title", "place", "date_expression", "time_expression", "end_date_expression", "end_time_expression", "link"],
                     "properties": {
                         "title": {"type": "string"}, "place": {"type": ["string", "null"]},
                         "date_expression": {"type": ["string", "null"]},
                         "time_expression": {"type": ["string", "null"]},
                         "end_date_expression": {"type": ["string", "null"]},
                         "end_time_expression": {"type": ["string", "null"]},
                         "link": {"type": ["string", "null"]},
                     }},
                    {"type": "object", "additionalProperties": False, "properties": {}},
                    {"type": "object", "additionalProperties": False, "required": ["category"],
                     "properties": {"category": {"enum": sorted(_UNSUPPORTED)}}},
                ]
            },
        },
    },
}


def decode_provider_envelope(raw: str) -> ParsedIntent:
    """Decode the provider shape {intent, arguments} into the strict flat union."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IntentParserInvalidOutput("invalid_json") from exc
    if not isinstance(value, dict) or set(value) != {"intent", "arguments"} or not isinstance(value["arguments"], dict):
        raise IntentParserInvalidOutput("invalid_envelope")
    return decode_intent({"intent": value["intent"], **value["arguments"]})
