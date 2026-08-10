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
    IntentKind.QUERY_PURCHASES: {"status": (str,), "priority": (str,), "buyer": (str,), "operation": (str,)},
    IntentKind.QUERY_FILMS: {"status": (str,), "media_type": (str,), "genre": (str, type(None)), "operation": (str,)},
    IntentKind.QUERY_CALENDAR: {"date_from": (str, type(None)), "date_to": (str, type(None)), "target": (str, type(None)), "operation": (str,)},
    IntentKind.QUERY_AFISHA: {"date_from": (str, type(None)), "date_to": (str, type(None)), "target": (str, type(None)), "operation": (str,)},
    IntentKind.NO_ACTION: {},
    IntentKind.UNSUPPORTED: {"category": (str,)},
}

_OPTIONAL_NULL_FIELDS = {
    "price", "priority", "link", "comment", "buyer", "date_expression", "time_expression",
    "end_time_expression", "place", "end_date_expression", "title", "status", "genre", "date_from", "date_to", "target",
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
    if kind is IntentKind.QUERY_PURCHASES:
        if arguments["status"] not in {"planned", "bought", "any"} or arguments["priority"] not in {"high", "medium", "low", "any"} or arguments["buyer"] not in {"current_user", "other_user", "unassigned", "any"} or arguments["operation"] not in {"list", "count", "sum"}:
            raise IntentParserInvalidOutput("invalid_query_arguments")
    if kind is IntentKind.QUERY_FILMS:
        if arguments["status"] not in {"want", "watched", "any"} or arguments["media_type"] not in {"movie", "tv", "any"} or arguments["operation"] not in {"list", "count", "random"}:
            raise IntentParserInvalidOutput("invalid_query_arguments")
    if kind in {IntentKind.QUERY_CALENDAR, IntentKind.QUERY_AFISHA} and arguments["operation"] not in {"list", "count", "next"}:
        raise IntentParserInvalidOutput("invalid_query_arguments")
    if kind is IntentKind.UNSUPPORTED and arguments["category"] not in _UNSUPPORTED:
        raise IntentParserInvalidOutput("invalid_category")
    return ParsedIntent(kind, arguments)


def _arguments_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


_BRANCH_PROPERTIES: dict[IntentKind, dict[str, Any]] = {
    IntentKind.ADD_MOVIE_OR_TV: {"query": {"type": "string"}},
    IntentKind.ADD_PURCHASE: {
        "title": {"type": "string"}, "price": {"type": ["integer", "null"], "minimum": 0, "maximum": 1_000_000_000},
        "priority": {"type": ["string", "null"], "enum": ["high", "medium", "low", None]}, "link": {"type": ["string", "null"]},
        "comment": {"type": ["string", "null"]}, "buyer": {"type": ["string", "null"], "enum": ["current_user", None]},
    },
    IntentKind.ADD_PERSONAL_CALENDAR_EVENT: {
        "title": {"type": "string"}, "date_expression": {"type": ["string", "null"]},
        "time_expression": {"type": ["string", "null"]}, "end_time_expression": {"type": ["string", "null"]},
        "comment": {"type": ["string", "null"]}, "owner": {"type": "string", "enum": ["current_user"]},
    },
    IntentKind.ADD_AFISHA_EVENT: {
        "title": {"type": "string"}, "place": {"type": ["string", "null"]},
        "date_expression": {"type": ["string", "null"]}, "time_expression": {"type": ["string", "null"]},
        "end_date_expression": {"type": ["string", "null"]}, "end_time_expression": {"type": ["string", "null"]},
        "link": {"type": ["string", "null"]},
    },
    IntentKind.UPDATE_PURCHASE: {"target": {"type": "string"}, "title": {"type": ["string", "null"]}, "price": {"type": ["integer", "null"], "minimum": 0, "maximum": 1_000_000_000}, "priority": {"type": ["string", "null"], "enum": ["high", "medium", "low", "none", None]}, "link": {"type": ["string", "null"]}, "comment": {"type": ["string", "null"]}, "buyer": {"type": ["string", "null"], "enum": ["current_user", "none", None]}, "status": {"type": ["string", "null"], "enum": ["planned", "bought", None]}},
    IntentKind.DELETE_PURCHASE: {"target": {"type": "string"}},
    IntentKind.UPDATE_FILM: {"target": {"type": "string"}, "status": {"type": ["string", "null"], "enum": ["want", "watched", None]}, "comment": {"type": ["string", "null"]}},
    IntentKind.DELETE_FILM: {"target": {"type": "string"}},
    IntentKind.UPDATE_CALENDAR_EVENT: {"target": {"type": "string"}, "title": {"type": ["string", "null"]}, "date_expression": {"type": ["string", "null"]}, "time_expression": {"type": ["string", "null"]}},
    IntentKind.DELETE_CALENDAR_EVENT: {"target": {"type": "string"}},
    IntentKind.UPDATE_AFISHA_EVENT: {"target": {"type": "string"}, "title": {"type": ["string", "null"]}, "date_expression": {"type": ["string", "null"]}, "time_expression": {"type": ["string", "null"]}},
    IntentKind.DELETE_AFISHA_EVENT: {"target": {"type": "string"}},
    IntentKind.QUERY_PURCHASES: {"status": {"type": "string", "enum": ["planned", "bought", "any"]}, "priority": {"type": "string", "enum": ["high", "medium", "low", "any"]}, "buyer": {"type": "string", "enum": ["current_user", "other_user", "unassigned", "any"]}, "operation": {"type": "string", "enum": ["list", "count", "sum"]}},
    IntentKind.QUERY_FILMS: {"status": {"type": "string", "enum": ["want", "watched", "any"]}, "media_type": {"type": "string", "enum": ["movie", "tv", "any"]}, "genre": {"type": ["string", "null"]}, "operation": {"type": "string", "enum": ["list", "count", "random"]}},
    IntentKind.QUERY_CALENDAR: {"date_from": {"type": ["string", "null"]}, "date_to": {"type": ["string", "null"]}, "target": {"type": ["string", "null"]}, "operation": {"type": "string", "enum": ["list", "count", "next"]}},
    IntentKind.QUERY_AFISHA: {"date_from": {"type": ["string", "null"]}, "date_to": {"type": ["string", "null"]}, "target": {"type": ["string", "null"]}, "operation": {"type": "string", "enum": ["list", "count", "next"]}},
    IntentKind.NO_ACTION: {},
    IntentKind.UNSUPPORTED: {"category": {"type": "string", "enum": sorted(_UNSUPPORTED)}},
}

# A nested discriminated union could bind intent to arguments, but would require
# changing the response envelope (for example, putting both values inside an
# anyOf branch).  Keeping the existing envelope with independent properties
# cannot establish that relationship.  Instead, GPT-4o sees one strict superset
# object.  Every field is nullable because it is irrelevant to at least one
# intent; the adapter below rejects non-null data outside the selected intent.
def _nullable_provider_property(name: str) -> dict[str, Any]:
    schemas = [properties[name] for properties in _BRANCH_PROPERTIES.values() if name in properties]
    result: dict[str, Any] = {"type": ["integer", "null"] if name == "price" else ["string", "null"]}
    if name == "price":
        result.update(minimum=0, maximum=1_000_000_000)
    enums = {item for schema in schemas for item in schema.get("enum", [])}
    if enums:
        result["enum"] = sorted((item for item in enums if item is not None), key=str) + [None]
    return result


_PROVIDER_FIELD_NAMES = sorted({name for properties in _BRANCH_PROPERTIES.values() for name in properties})
_PROVIDER_PROPERTIES = {name: _nullable_provider_property(name) for name in _PROVIDER_FIELD_NAMES}


INTENT_JSON_SCHEMA: dict[str, Any] = {
    "name": "telegram_bot_intent",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["intent", "arguments"],
        "properties": {
            "intent": {"type": "string", "enum": [kind.value for kind in IntentKind]},
            "arguments": _arguments_schema(_PROVIDER_PROPERTIES),
        },
    },
}


def normalize_provider_envelope(raw: str | dict[str, Any]) -> dict[str, Any]:
    """Validate the provider superset and return the exact canonical envelope."""
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError) as exc:
        raise IntentParserInvalidOutput("invalid_json") from exc
    if not isinstance(value, dict) or set(value) != {"intent", "arguments"}:
        raise IntentParserInvalidOutput("invalid_envelope")
    intent, supplied = value["intent"], value["arguments"]
    if not isinstance(intent, str) or not isinstance(supplied, dict):
        raise IntentParserInvalidOutput("invalid_envelope")
    try:
        kind = IntentKind(intent)
    except ValueError as exc:
        raise IntentParserInvalidOutput("unsupported_intent") from exc
    if set(supplied) != set(_PROVIDER_FIELD_NAMES):
        raise IntentParserInvalidOutput("invalid_provider_fields")

    allowed = _FIELDS[kind]
    for name, item in supplied.items():
        if name not in allowed and item is not None:
            raise IntentParserInvalidOutput("irrelevant_non_null_field")
        schema = _PROVIDER_PROPERTIES[name]
        valid_type = item is None or (name == "price" and isinstance(item, int) and not isinstance(item, bool)) or (
            name != "price" and isinstance(item, str)
        )
        if not valid_type:
            raise IntentParserInvalidOutput(f"invalid_provider_{name}")
        if "enum" in schema and item not in schema["enum"]:
            raise IntentParserInvalidOutput(f"invalid_provider_{name}")
        if name == "price" and item is not None and not 0 <= item <= 1_000_000_000:
            raise IntentParserInvalidOutput("invalid_provider_price")
    return {"intent": intent, "arguments": {name: supplied[name] for name in allowed}}


def decode_provider_envelope(raw: str) -> ParsedIntent:
    return decode_intent(normalize_provider_envelope(raw))
