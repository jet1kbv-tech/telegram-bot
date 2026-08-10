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

_PROVIDER_FIELD_NAMES = sorted({name for properties in _BRANCH_PROPERTIES.values() for name in properties})


INTENT_JSON_SCHEMA: dict[str, Any] = {
    "name": "telegram_bot_intent",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["intent", "arguments"],
        "properties": {
            "intent": {"type": "string", "enum": [kind.value for kind in IntentKind]},
            "arguments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "value"],
                    "properties": {
                        "name": {"type": "string", "enum": _PROVIDER_FIELD_NAMES},
                        "value": {"type": "string", "minLength": 1, "maxLength": 1000},
                    },
                },
                "maxItems": len(_PROVIDER_FIELD_NAMES),
            },
        },
    },
}


def normalize_provider_envelope(raw: str | dict[str, Any]) -> dict[str, Any]:
    """Turn the compact provider semantics into the exact canonical envelope."""
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError) as exc:
        raise IntentParserInvalidOutput("invalid_json") from exc
    if not isinstance(value, dict) or set(value) != {"intent", "arguments"}:
        raise IntentParserInvalidOutput("invalid_envelope")
    intent = value["intent"]
    items = value["arguments"]
    if not isinstance(intent, str) or not isinstance(items, list) or len(items) > len(_PROVIDER_FIELD_NAMES):
        raise IntentParserInvalidOutput("invalid_envelope")
    try:
        kind = IntentKind(intent)
    except ValueError as exc:
        raise IntentParserInvalidOutput("unsupported_intent") from exc
    allowed = _FIELDS[kind]
    supplied: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"name", "value"}:
            raise IntentParserInvalidOutput("invalid_argument_entry")
        name, raw_value = item["name"], item["value"]
        if name not in _PROVIDER_FIELD_NAMES or not isinstance(raw_value, str):
            raise IntentParserInvalidOutput("invalid_argument_entry")
        raw_value = raw_value.strip()
        if not raw_value or len(raw_value) > 1000:
            raise IntentParserInvalidOutput(f"invalid_provider_{name}")
        if name in supplied:
            raise IntentParserInvalidOutput("duplicate_provider_field")
        supplied[name] = raw_value

    # These are narrow, intent-scoped equivalences, not a general policy of
    # discarding unrelated values.  They cover vocabulary the model naturally
    # uses while retaining fail-closed handling for every other mismatch.
    if kind is IntentKind.ADD_MOVIE_OR_TV and "title" in supplied:
        if "query" in supplied:
            raise IntentParserInvalidOutput("conflicting_provider_fields")
        supplied["query"] = supplied.pop("title")
    if kind in {IntentKind.QUERY_CALENDAR, IntentKind.UPDATE_CALENDAR_EVENT} and "owner" in supplied:
        if supplied.pop("owner") != "current_user":
            raise IntentParserInvalidOutput("irrelevant_non_null_field")

    conflicts = set(supplied) - set(allowed)
    if conflicts:
        raise IntentParserInvalidOutput("irrelevant_non_null_field")

    arguments: dict[str, Any] = {name: None for name in allowed}
    arguments.update(supplied)
    if "price" in supplied:
        if not supplied["price"].isdigit():
            raise IntentParserInvalidOutput("invalid_provider_price")
        arguments["price"] = int(supplied["price"])
    # Unsupported is non-mutating.  Collapsing an unfamiliar taxonomy label to
    # the canonical catch-all cannot authorize an action and avoids coupling the
    # provider's wording to internal UI categories.
    if kind is IntentKind.UNSUPPORTED and arguments["category"] not in _UNSUPPORTED:
        arguments["category"] = "unsupported_domain"
    return {"intent": intent, "arguments": arguments}


def provider_rejection_shape(raw: str) -> tuple[str, list[str]]:
    """Return only safe structural details for a normalization rejection."""
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return "<unavailable>", []
    if not isinstance(value, dict) or not isinstance(value.get("intent"), str):
        return "<invalid>", []
    intent = value["intent"]
    try:
        kind = IntentKind(intent)
        allowed = _FIELDS[kind]
    except ValueError:
        return intent, []
    items = value.get("arguments")
    if not isinstance(items, list):
        return intent, []
    names = [item.get("name") for item in items if isinstance(item, dict)]
    benign = {"owner"} if kind in {IntentKind.QUERY_CALENDAR, IntentKind.UPDATE_CALENDAR_EVENT} else set()
    aliases = {"title"} if kind is IntentKind.ADD_MOVIE_OR_TV else set()
    known = set(allowed) | benign | aliases
    return intent, sorted({name for name in names if isinstance(name, str) and name not in known})


def decode_provider_envelope(raw: str) -> ParsedIntent:
    return decode_intent(normalize_provider_envelope(raw))
