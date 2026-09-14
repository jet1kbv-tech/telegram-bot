from datetime import datetime, timezone

import pytest

from bot.services.nl_deterministic_routing import (
    named_event_question, short_context_follow_up, typed_event_document_query,
)
from bot.services.nl_intent import IntentKind


NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


def parse_named(text, afisha):
    data = {"calendars": {"vova": [], "sasha": []}, "afisha": afisha, "event_attachments": []}
    return named_event_question(text, data=data, actor_key="vova", now=NOW, timezone="UTC")


def test_production_event_and_trip_follow_up_forms_are_domain_bounded():
    assert short_context_follow_up("А где?", "event").arguments["query_type"] == "event_place"
    assert short_context_follow_up("А документы?", "event").arguments["query_type"] == "documents"
    assert short_context_follow_up("А обратно?", "trip").arguments["query_type"] == "return"
    assert short_context_follow_up("А билеты?", "trip").arguments["query_type"] == "documents"
    assert short_context_follow_up("А обратно?", "event") is None


def test_named_event_questions_never_supply_an_invented_date():
    cases = {
        "Когда ТЕСТ — Купить сувениры?": ("event_date", "ТЕСТ — Купить сувениры"),
        "Во сколько ТЕСТ — Купить сувениры?": ("event_time", "ТЕСТ — Купить сувениры"),
        "Где ТЕСТ — Эрмитаж?": ("event_place", "ТЕСТ — Эрмитаж"),
    }
    for text, expected in cases.items():
        title = expected[1]
        parsed = parse_named(text, [{
            "id": text, "title": title, "date": "2026-09-20", "time": "12:00", "status": "active",
        }])
        assert (parsed.arguments["query_type"], parsed.arguments["target"]) == expected
        assert parsed.arguments["date_expression"] is None


def test_unmatched_general_questions_fall_through_to_provider():
    assert parse_named("Когда Новый год?", []) is None
    assert parse_named("Где ресторан Пушкин?", []) is None


def test_ambiguous_named_event_falls_through_to_provider():
    events = [
        {"id": "one", "title": "Концерт", "date": "2026-09-20", "time": "12:00", "status": "active"},
        {"id": "two", "title": "Концерт", "date": "2026-09-21", "time": "12:00", "status": "active"},
    ]
    assert parse_named("Когда концерт?", events) is None


@pytest.mark.parametrize(("text", "semantic_type"), [
    ("События с бронью", "reservation"),
    ("События с бронированием", "reservation"),
    ("События с билетом", "transport_ticket"),
    ("События с билетами", "transport_ticket"),
    ("События с билет", "transport_ticket"),
    ("События со страховкой", "insurance"),
    ("События с страховка", "insurance"),
])
def test_typed_event_document_query_is_bounded_and_canonical(text, semantic_type):
    parsed = typed_event_document_query(text)
    assert parsed.intent is IntentKind.QUERY_CONTEXT
    assert parsed.arguments == {
        "query_type": "events_with_document_type", "destination": None,
        "transport_type": None, "target": None, "date_expression": None,
        "person": None, "semantic_type": semantic_type, "follow_up": False,
    }


@pytest.mark.parametrize("text", ["События с документами", "События с паспортами"])
def test_generic_or_unknown_event_document_query_falls_through(text):
    assert typed_event_document_query(text) is None
