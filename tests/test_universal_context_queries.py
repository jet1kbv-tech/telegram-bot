from copy import deepcopy
from datetime import datetime, timezone

import pytest

from bot.services.context_queries import query_context
from bot.services.nl_intent import IntentKind, IntentParserInvalidOutput
from bot.services.nl_intent_decoder import decode_provider_envelope


NOW = datetime(2026, 9, 9, 9, tzinfo=timezone.utc)  # Wednesday


def data():
    return {
        "calendars": {
            "vova": [
                {"id": "v1", "title": "Ужин", "date": "2026-09-10", "start_time": "19:00", "source": "manual"},
                {"id": "projection", "title": "Концерт", "date": "2026-09-12", "start_time": "19:30", "source": "afisha", "source_event_id": "a1"},
            ],
            "sasha": [{"id": "s1", "title": "Секрет Саши", "date": "2026-09-10", "start_time": "10:00", "source": "manual"}],
        },
        "afisha": [
            {"id": "a1", "title": "Концерт", "date": "2026-09-12", "time": "19:30", "place": "VK Stadium", "status": "active"},
            {"id": "a2", "title": "Обед", "date": "2026-09-12", "time": "14:00", "status": "active"},
        ],
        "event_attachments": [],
    }


def ask(snapshot, kind, **arguments):
    return query_context(snapshot, actor_key="vova", now=NOW, timezone="Europe/Moscow",
                         query_type=kind, **arguments)


@pytest.mark.parametrize(("expression", "expected"), [
    ("завтра", "Ужин"), ("12 сентября", "Концерт"), ("выходные", "Обед"),
    ("следующая неделя", "ничего не запланировано"),
])
def test_schedule_ranges_and_empty_state(expression, expected):
    assert expected in ask(data(), "events", date_expression=expression).text


def test_schedule_is_sorted_and_projection_is_not_duplicated():
    text = ask(data(), "events", date_expression="выходные").text
    assert text.index("14:00") < text.index("19:30")
    assert text.count("Концерт") == 1


def test_person_argument_cannot_reveal_other_private_calendar():
    snapshot = data()
    before = deepcopy(snapshot)
    answer = ask(snapshot, "events", date_expression="завтра", person="sasha")
    assert "Ужин" in answer.text
    assert "Секрет Саши" not in answer.text
    assert snapshot == before


def test_actor_scoping_is_symmetric_and_shared_afisha_remains_visible():
    snapshot = data()
    answer = query_context(snapshot, actor_key="sasha", now=NOW, timezone="Europe/Moscow",
                           query_type="events", date_expression="выходные", person="vova")
    assert "Концерт" in answer.text and "Ужин" not in answer.text


def test_next_matching_event_and_not_found():
    result = ask(data(), "next_event", target="концерт")
    assert result.outcome == "found" and "12 сентября" in result.text
    assert ask(data(), "next_event", target="опера").outcome == "not_found"


@pytest.mark.parametrize(("kind", "expected"), [
    ("event_time", "19:30"), ("event_date", "12 сентября"), ("event_place", "VK Stadium"),
])
def test_event_fields(kind, expected):
    assert expected in ask(data(), kind, target="концерт").text


def test_missing_place_and_ambiguous_target():
    assert "место" in ask(data(), "event_place", target="обед").text
    snapshot = data()
    snapshot["afisha"].append({"id": "a3", "title": "Концерт", "date": "2026-09-28", "time": "20:00", "status": "active"})
    answer = ask(snapshot, "event_time", target="концерт")
    assert answer.outcome == "ambiguous" and "1. Концерт" in answer.text and "2. Концерт" in answer.text


def test_event_documents_use_labels_and_never_transport_identifiers():
    snapshot = data()
    snapshot["event_attachments"] = [{
        "id": "secret-attachment", "parent_type": "afisha", "parent_event_id": "a1",
        "semantic_type": "transport_ticket", "transport_type": "train",
        "telegram_file_id": "SECRET-FILE-ID", "telegram_media_type": "document",
    }]
    answer = ask(snapshot, "event_documents", target="концерт")
    assert "билет на поезд" in answer.text
    assert "secret-attachment" not in answer.text and "SECRET-FILE-ID" not in answer.text
    assert "не прикреплено" in ask(data(), "event_documents", target="концерт").text


def test_extended_provider_contract_and_fail_closed_person():
    parsed = decode_provider_envelope({"intent": "query_context", "arguments": [
        {"name": "query_type", "value": "events"},
        {"name": "date_expression", "value": "завтра"},
        {"name": "person", "value": "sasha"},
    ]})
    assert parsed.intent is IntentKind.QUERY_CONTEXT
    assert parsed.arguments["date_expression"] == "завтра"
    with pytest.raises(IntentParserInvalidOutput, match="invalid_person"):
        decode_provider_envelope({"intent": "query_context", "arguments": [
            {"name": "query_type", "value": "events"},
            {"name": "date_expression", "value": "завтра"},
            {"name": "person", "value": "admin"},
        ]})
