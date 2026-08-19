from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from bot.services.nl_intent import IntentKind, IntentParserInvalidOutput, ParsedIntent
from bot.services.genre_vocabulary import canonicalize_genres


# Shared by the canonical decoder and the semantic branch schema.  The compact
# Polza wire schema cannot condition an argument value on its sibling name, so
# enum enforcement happens at this boundary after the wire envelope is
# normalized.
TRANSPORT_TYPES = ("train", "plane", "bus", "other")
_TRANSPORT_VALUES = {None, *TRANSPORT_TYPES}


def normalize_recommendation_genres(values: list[str]) -> list[str]:
    """Allow-list provider genre output; unsupported values are ignored."""
    return list(canonicalize_genres(values))

# This is the canonical provider/domain boundary.  The schema below is generated
# from the same definitions that the decoder uses, so the two cannot silently
# acquire different fields.
_FIELDS: dict[IntentKind, dict[str, tuple[type, ...]]] = {
    IntentKind.RECOMMEND_FILM: {
        "actor": (str, type(None)), "source": (str, type(None)), "media_type": (str, type(None)),
        "include_genres": (list,), "exclude_genres": (list,), "min_year": (int, type(None)),
        "max_year": (int, type(None)), "min_rating": (int, float, type(None)),
        "max_runtime": (int, type(None)), "language": (str, type(None)), "country": (str, type(None)),
    },
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
    IntentKind.ATTACH_EVENT_FILE: {
        "target": (str,), "semantic_type": (str, type(None)), "transport_type": (str, type(None)),
        "origin": (str, type(None)), "destination": (str, type(None)),
        "date_expression": (str, type(None)), "departure_time": (str, type(None)), "person": (str, type(None)),
    },
    IntentKind.QUERY_EVENT_ATTACHMENTS: {
        "target": (str, type(None)), "semantic_type": (str, type(None)),
        "transport_type": (str, type(None)), "origin": (str, type(None)),
        "destination": (str, type(None)), "date": (str, type(None)),
        "person": (str, type(None)), "direction": (str, type(None)), "return_all": (bool,),
    },
    IntentKind.QUERY_CONTEXT: {
        "query_type": (str,), "destination": (str, type(None)),
        "transport_type": (str, type(None)),
    },
    IntentKind.DELETE_EVENT_ATTACHMENT: {
        "target": (str, type(None)), "semantic_type": (str, type(None)), "transport_type": (str, type(None)),
        "origin": (str, type(None)), "destination": (str, type(None)), "date": (str, type(None)),
        "person": (str, type(None)), "direction": (str, type(None)),
    },
    IntentKind.UPDATE_EVENT_ATTACHMENT: {
        "target": (str, type(None)), "semantic_type": (str, type(None)), "transport_type": (str, type(None)),
        "origin": (str, type(None)), "destination": (str, type(None)), "date": (str, type(None)),
        "person": (str, type(None)), "direction": (str, type(None)),
        "new_origin": (str, type(None)), "new_destination": (str, type(None)), "new_date": (str, type(None)),
        "new_departure_time": (str, type(None)), "new_arrival_date": (str, type(None)),
        "new_arrival_time": (str, type(None)), "new_person": (str, type(None)),
    },
    IntentKind.QUERY_PURCHASES: {"status": (str,), "priority": (str,), "buyer": (str,), "operation": (str,)},
    IntentKind.QUERY_FILMS: {"status": (str,), "media_type": (str,), "genre": (str, type(None)), "operation": (str,)},
    IntentKind.QUERY_CALENDAR: {"date_from": (str, type(None)), "date_to": (str, type(None)), "target": (str, type(None)), "operation": (str,)},
    IntentKind.QUERY_AFISHA: {"date_from": (str, type(None)), "date_to": (str, type(None)), "target": (str, type(None)), "operation": (str,)},
    IntentKind.NO_ACTION: {},
    IntentKind.UNSUPPORTED: {"category": (str,)},
}

_OPTIONAL_NULL_FIELDS = {
    "price", "priority", "link", "comment", "buyer", "date_expression", "time_expression",
    "end_time_expression", "departure_time", "place", "end_date_expression", "title", "status", "genre", "date_from", "date_to", "target",
    "semantic_type", "transport_type", "origin", "destination", "date", "person", "direction",
    "new_origin", "new_destination", "new_date", "new_departure_time", "new_arrival_date", "new_arrival_time", "new_person",
}
_UNSUPPORTED = {"destructive", "other_user_calendar", "bulk", "unsupported_domain", "conversation"}
_PROVIDER_PRICE = re.compile(
    r"(?:[0-9]+|[0-9]{1,3}(?P<separator>[ _\u00a0])[0-9]{3}(?:(?P=separator)[0-9]{3})*)"
    r"(?:\s*(?:₽|руб\.?|рублей))?",
    re.IGNORECASE,
)
_SAFE_PROVIDER_OPERATION = re.compile(r"[A-Za-z0-9_-]{1,40}")

_PURCHASE_PRIORITY_ALIASES = {
    "высокий": "high", "высокая": "high", "высокое": "high",
    "средний": "medium", "средняя": "medium", "среднее": "medium",
    "низкий": "low", "низкая": "low", "низкое": "low",
}

# Defaults here are part of existing domain behavior, not inferred user data.
# Every query renderer treats ``list`` as its ordinary operation, while the
# native purchase flow represents a skipped priority as no priority (``None``
# at the canonical boundary and an empty string in storage).
_PROVIDER_TECHNICAL_DEFAULTS: dict[IntentKind, dict[str, Any]] = {
    IntentKind.RECOMMEND_FILM: {"actor": "self", "source": "external", "media_type": "any",
        "include_genres": [], "exclude_genres": []},
    IntentKind.ADD_PURCHASE: {"priority": None},
    IntentKind.QUERY_PURCHASES: {
        "status": "planned", "priority": "any", "buyer": "any", "operation": "list",
    },
    IntentKind.QUERY_FILMS: {
        "status": "want", "media_type": "any", "genre": None, "operation": "list",
    },
    IntentKind.QUERY_CALENDAR: {"operation": "list"},
    IntentKind.QUERY_AFISHA: {"operation": "list"},
    IntentKind.QUERY_EVENT_ATTACHMENTS: {"return_all": False},
}


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
        if (isinstance(item, bool) and bool not in types) or not isinstance(item, types):
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
    if kind is IntentKind.RECOMMEND_FILM:
        if arguments["actor"] not in {None, "self", "vova", "sasha", "both"} or arguments["source"] not in {None, "external", "want"} or arguments["media_type"] not in {None, "movie", "tv", "any"}:
            raise IntentParserInvalidOutput("invalid_recommendation_mode")
        for key in ("include_genres", "exclude_genres"):
            if not isinstance(arguments[key], list) or any(not isinstance(x, str) for x in arguments[key]): raise IntentParserInvalidOutput("invalid_genres")
            arguments[key] = normalize_recommendation_genres(arguments[key])
        if arguments["min_year"] is not None and not 1888 <= arguments["min_year"] <= 2100: raise IntentParserInvalidOutput("invalid_min_year")
        if arguments["max_year"] is not None and not 1888 <= arguments["max_year"] <= 2100: raise IntentParserInvalidOutput("invalid_max_year")
        if arguments["min_year"] and arguments["max_year"] and arguments["min_year"] > arguments["max_year"]: raise IntentParserInvalidOutput("invalid_year_range")
        if arguments["min_rating"] is not None and not 0 <= arguments["min_rating"] <= 10: raise IntentParserInvalidOutput("invalid_min_rating")
        if arguments["max_runtime"] is not None and not 1 <= arguments["max_runtime"] <= 600: raise IntentParserInvalidOutput("invalid_max_runtime")
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
    if kind is IntentKind.QUERY_CONTEXT:
        if arguments["query_type"] not in {"departure", "arrival", "return", "documents", "overview"}:
            raise IntentParserInvalidOutput("invalid_query_type")
        if arguments["transport_type"] not in _TRANSPORT_VALUES:
            raise IntentParserInvalidOutput("invalid_transport_type")
        if arguments["destination"] is not None and not arguments["destination"]:
            raise IntentParserInvalidOutput("empty_destination")
    if kind is IntentKind.UNSUPPORTED and arguments["category"] not in _UNSUPPORTED:
        raise IntentParserInvalidOutput("invalid_category")
    if kind is IntentKind.ATTACH_EVENT_FILE:
        if arguments["semantic_type"] not in {None, "transport_ticket", "voucher", "accommodation", "insurance", "reservation", "other"}:
            raise IntentParserInvalidOutput("invalid_semantic_type")
        if arguments["transport_type"] not in _TRANSPORT_VALUES:
            raise IntentParserInvalidOutput("invalid_transport_type")
        if arguments["person"] not in {None, "current_user", "other_user", "both"}:
            raise IntentParserInvalidOutput("invalid_person")
        if arguments["departure_time"] is not None:
            try:
                arguments["departure_time"] = datetime.strptime(arguments["departure_time"], "%H:%M").strftime("%H:%M")
            except ValueError as exc:
                raise IntentParserInvalidOutput("invalid_departure_time") from exc
    if kind in {IntentKind.QUERY_EVENT_ATTACHMENTS, IntentKind.DELETE_EVENT_ATTACHMENT, IntentKind.UPDATE_EVENT_ATTACHMENT}:
        if arguments["semantic_type"] not in {None, "transport_ticket", "voucher", "reservation", "insurance", "other"}:
            raise IntentParserInvalidOutput("invalid_semantic_type")
        if arguments["transport_type"] not in _TRANSPORT_VALUES:
            raise IntentParserInvalidOutput("invalid_transport_type")
        if arguments["person"] not in {None, "current_user", "other_user", "both"}:
            raise IntentParserInvalidOutput("invalid_person")
        if arguments["direction"] not in {None, "outbound", "return"}:
            raise IntentParserInvalidOutput("invalid_direction")
        if kind is not IntentKind.UPDATE_EVENT_ATTACHMENT and arguments["date"] is not None:
            try: arguments["date"] = datetime.strptime(arguments["date"], "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError as exc: raise IntentParserInvalidOutput("invalid_date") from exc
        if kind is IntentKind.UPDATE_EVENT_ATTACHMENT and arguments["new_person"] not in {None, "current_user", "other_user", "both"}:
            raise IntentParserInvalidOutput("invalid_new_person")
    return ParsedIntent(kind, arguments)


_BRANCH_PROPERTIES: dict[IntentKind, dict[str, Any]] = {
    IntentKind.RECOMMEND_FILM: {
        "actor": {"type": ["string", "null"], "enum": ["self", "vova", "sasha", "both", None]},
        "source": {"type": ["string", "null"], "enum": ["external", "want", None]},
        "media_type": {"type": ["string", "null"], "enum": ["movie", "tv", "any", None]},
        "include_genres": {"type": "array", "items": {"type": "string"}},
        "exclude_genres": {"type": "array", "items": {"type": "string"}},
        "min_year": {"type": ["integer", "null"]}, "max_year": {"type": ["integer", "null"]},
        "min_rating": {"type": ["number", "null"]}, "max_runtime": {"type": ["integer", "null"]},
        "language": {"type": ["string", "null"]}, "country": {"type": ["string", "null"]},
    },
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
    IntentKind.ATTACH_EVENT_FILE: {
        "target": {"type": "string"},
        "semantic_type": {"type": ["string", "null"], "enum": ["transport_ticket", "voucher", "accommodation", "insurance", "reservation", "other", None]},
        "transport_type": {"type": ["string", "null"], "enum": [*TRANSPORT_TYPES, None]},
        "origin": {"type": ["string", "null"]}, "destination": {"type": ["string", "null"]},
        "date_expression": {"type": ["string", "null"]},
        "departure_time": {"type": ["string", "null"]},
        "person": {"type": ["string", "null"], "enum": ["current_user", "other_user", "both", None]},
    },
    IntentKind.QUERY_EVENT_ATTACHMENTS: {
        "target": {"type": ["string", "null"]},
        "semantic_type": {"type": ["string", "null"], "enum": ["transport_ticket", "voucher", "reservation", "insurance", "other", None]},
        "transport_type": {"type": ["string", "null"], "enum": [*TRANSPORT_TYPES, None]},
        "origin": {"type": ["string", "null"]}, "destination": {"type": ["string", "null"]},
        "date": {"type": ["string", "null"]}, "person": {"type": ["string", "null"], "enum": ["current_user", "other_user", "both", None]},
        "direction": {"type": ["string", "null"], "enum": ["outbound", "return", None]},
        "return_all": {"type": "boolean"},
    },
    IntentKind.QUERY_CONTEXT: {
        "query_type": {"type": "string", "enum": ["departure", "arrival", "return", "documents", "overview"]},
        "destination": {"type": ["string", "null"]},
        "transport_type": {"type": ["string", "null"], "enum": [*TRANSPORT_TYPES, None]},
    },
    IntentKind.QUERY_PURCHASES: {"status": {"type": "string", "enum": ["planned", "bought", "any"]}, "priority": {"type": "string", "enum": ["high", "medium", "low", "any"]}, "buyer": {"type": "string", "enum": ["current_user", "other_user", "unassigned", "any"]}, "operation": {"type": "string", "enum": ["list", "count", "sum"]}},
    IntentKind.QUERY_FILMS: {"status": {"type": "string", "enum": ["want", "watched", "any"]}, "media_type": {"type": "string", "enum": ["movie", "tv", "any"]}, "genre": {"type": ["string", "null"]}, "operation": {"type": "string", "enum": ["list", "count", "random"]}},
    IntentKind.QUERY_CALENDAR: {"date_from": {"type": ["string", "null"]}, "date_to": {"type": ["string", "null"]}, "target": {"type": ["string", "null"]}, "operation": {"type": "string", "enum": ["list", "count", "next"]}},
    IntentKind.QUERY_AFISHA: {"date_from": {"type": ["string", "null"]}, "date_to": {"type": ["string", "null"]}, "target": {"type": ["string", "null"]}, "operation": {"type": "string", "enum": ["list", "count", "next"]}},
    IntentKind.NO_ACTION: {},
    IntentKind.UNSUPPORTED: {"category": {"type": "string", "enum": sorted(_UNSUPPORTED)}},
}

_ATTACHMENT_IDENTIFY_SCHEMA = {
    key: value for key, value in _BRANCH_PROPERTIES[IntentKind.QUERY_EVENT_ATTACHMENTS].items()
    if key != "return_all"
}
_BRANCH_PROPERTIES[IntentKind.DELETE_EVENT_ATTACHMENT] = dict(_ATTACHMENT_IDENTIFY_SCHEMA)
_BRANCH_PROPERTIES[IntentKind.UPDATE_EVENT_ATTACHMENT] = {
    **_ATTACHMENT_IDENTIFY_SCHEMA,
    "new_origin": {"type": ["string", "null"]}, "new_destination": {"type": ["string", "null"]},
    "new_date": {"type": ["string", "null"]}, "new_departure_time": {"type": ["string", "null"]},
    "new_arrival_date": {"type": ["string", "null"]}, "new_arrival_time": {"type": ["string", "null"]},
    "new_person": {"type": ["string", "null"], "enum": ["current_user", "other_user", "both", None]},
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
    supplied: dict[str, Any] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"name", "value"}:
            raise IntentParserInvalidOutput("invalid_argument_entry")
        name, raw_value = item["name"], item["value"]
        if name not in _PROVIDER_FIELD_NAMES:
            raise IntentParserInvalidOutput("invalid_argument_entry")
        if name in supplied:
            raise IntentParserInvalidOutput("duplicate_provider_field")
        if raw_value is None and name in {"priority", "buyer"} and kind in {
            IntentKind.ADD_PURCHASE, IntentKind.UPDATE_PURCHASE,
        }:
            supplied[name] = None
            continue
        if not isinstance(raw_value, str):
            raise IntentParserInvalidOutput("invalid_argument_entry")
        raw_value = raw_value.strip()
        if not raw_value or len(raw_value) > 1000:
            raise IntentParserInvalidOutput(f"invalid_provider_{name}")
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
    for name, default in _PROVIDER_TECHNICAL_DEFAULTS.get(kind, {}).items():
        if name not in supplied:
            arguments[name] = default
    if kind is IntentKind.RECOMMEND_FILM:
        for name in ("include_genres", "exclude_genres"):
            raw_genres = supplied.get(name, "")
            arguments[name] = [part.strip() for part in raw_genres.split(",") if part.strip()] if isinstance(raw_genres, str) else []
        for name in ("min_year", "max_year", "max_runtime"):
            if name in supplied:
                try: arguments[name] = int(supplied[name])
                except ValueError as exc: raise IntentParserInvalidOutput(f"invalid_{name}") from exc
        if "min_rating" in supplied:
            try: arguments["min_rating"] = float(supplied["min_rating"].replace(",", "."))
            except ValueError as exc: raise IntentParserInvalidOutput("invalid_min_rating") from exc
    if supplied.get("priority") == "null" and kind in {
        IntentKind.ADD_PURCHASE, IntentKind.UPDATE_PURCHASE,
    }:
        arguments["priority"] = None
    elif "priority" in supplied and isinstance(supplied["priority"], str) and kind in {
        IntentKind.ADD_PURCHASE, IntentKind.UPDATE_PURCHASE, IntentKind.QUERY_PURCHASES,
    }:
        priority = supplied["priority"].casefold()
        arguments["priority"] = _PURCHASE_PRIORITY_ALIASES.get(priority, priority)
    if supplied.get("buyer") == "null" and kind in {
        IntentKind.ADD_PURCHASE, IntentKind.UPDATE_PURCHASE,
    }:
        arguments["buyer"] = None
    if "price" in supplied:
        match = _PROVIDER_PRICE.fullmatch(supplied["price"])
        if match is None:
            raise IntentParserInvalidOutput("invalid_provider_price")
        numeric = re.sub(r"[ _\u00a0]", "", match.group(0))
        numeric = re.sub(r"\s*(?:₽|руб\.?|рублей)$", "", numeric, flags=re.IGNORECASE)
        arguments["price"] = int(numeric)
    if kind is IntentKind.QUERY_EVENT_ATTACHMENTS and "return_all" in supplied:
        if supplied["return_all"].casefold() not in {"true", "false"}:
            raise IntentParserInvalidOutput("invalid_return_all")
        arguments["return_all"] = supplied["return_all"].casefold() == "true"
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


def provider_rejection_diagnostics(raw: str, reason: str) -> tuple[str, str]:
    """Return bounded diagnostics for the two semantic normalization failures."""
    try:
        value = json.loads(raw)
        items = value.get("arguments") if isinstance(value, dict) else None
    except (json.JSONDecodeError, TypeError):
        items = None
    if not isinstance(items, list):
        return "unknown", "unknown"
    fields = {
        item.get("name"): item.get("value")
        for item in items
        if isinstance(item, dict) and set(item) == {"name", "value"}
    }
    if reason == "invalid_operation":
        operation = fields.get("operation")
        if isinstance(operation, str) and _SAFE_PROVIDER_OPERATION.fullmatch(operation):
            return operation, "unknown"
    if reason == "invalid_provider_price":
        price = fields.get("price")
        if isinstance(price, str):
            if re.search(r"^[+-]", price):
                category = "signed"
            elif re.search(r"\d[.,]\d", price):
                category = "decimal"
            elif price.isdigit():
                category = "digits_only"
            elif "₽" in price or re.search(r"руб", price, re.IGNORECASE):
                category = "contains_currency"
            elif any(character.isspace() for character in price):
                category = "contains_spaces"
            elif any(character.isalpha() for character in price):
                category = "contains_letters"
            else:
                category = "other"
            return "unknown", category
    return "unknown", "unknown"


def decode_provider_envelope(raw: str) -> ParsedIntent:
    return decode_intent(normalize_provider_envelope(raw))
