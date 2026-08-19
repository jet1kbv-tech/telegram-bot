from datetime import datetime, timezone

from bot.services.context_queries import query_context
from bot.services.nl_intent import IntentKind, IntentParserInvalidOutput
from bot.services.nl_intent_decoder import decode_intent, decode_provider_envelope
import pytest

NOW = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)


def snapshot(*attachments):
    return {"calendars": {"vova": [], "sasha": []}, "afisha": [{"id": "a", "title": "Поездка", "date": "2026-08-30", "time": "20:00", "status": "active"}], "event_attachments": list(attachments)}


def ticket(identity="out", **changes):
    row = {"id": identity, "parent_type": "afisha", "parent_event_id": "a", "semantic_type": "transport_ticket", "transport_type": "train", "origin": "Москва Казанская", "destination": "Придача Воронеж-Южный", "date": "2026-08-30", "departure_time": "23:38", "arrival_date": "2026-08-31", "arrival_time": "09:33", "telegram_media_type": "document"}
    row.update(changes)
    return row


@pytest.mark.parametrize(("kind", "expected"), [("departure", "Отправление"), ("arrival", "Прибытие"), ("overview", "Документы: 1"), ("documents", "Документы: 1")])
def test_local_context_answers(kind, expected):
    result = query_context(snapshot(ticket()), actor_key="vova", now=NOW, timezone="Europe/Moscow", query_type=kind, destination="Воронеж", transport_type=None)
    assert result.outcome == "found" and expected in result.text


def test_arrival_never_derived_from_departure():
    result = query_context(snapshot(ticket(arrival_date=None, arrival_time=None)), actor_key="vova", now=NOW, timezone="UTC", query_type="arrival", destination="Воронеж")
    assert result.outcome == "missing" and "09:33" not in result.text and "23:38" not in result.text


def test_exact_opposite_route_is_return_and_creation_order_irrelevant():
    back = ticket("back", origin="Придача Воронеж-Южный", destination="Москва Казанская", date="2026-09-05", departure_time="18:00", arrival_date="2026-09-06", arrival_time="08:00")
    result = query_context(snapshot(back, ticket()), actor_key="sasha", now=NOW, timezone="UTC", query_type="return", destination="Воронеж")
    assert result.outcome == "found" and "18:00" in result.text


def test_private_calendar_is_actor_scoped():
    data = snapshot(ticket(parent_type="calendar", parent_event_id="v"))
    data["afisha"] = []
    data["calendars"]["vova"] = [{"id": "v", "title": "Private", "date": "2026-08-30", "start_time": "20:00", "source": "manual"}]
    assert query_context(data, actor_key="vova", now=NOW, timezone="UTC", query_type="departure", destination="Воронеж").outcome == "found"
    assert query_context(data, actor_key="sasha", now=NOW, timezone="UTC", query_type="departure", destination="Воронеж").outcome == "not_found"


def test_context_decoder_is_strict_and_bounded():
    parsed = decode_provider_envelope('{"intent":"query_context","arguments":[{"name":"query_type","value":"arrival"},{"name":"destination","value":"Воронеж"}]}')
    assert parsed.intent is IntentKind.QUERY_CONTEXT
    assert parsed.arguments == {"query_type": "arrival", "destination": "Воронеж", "transport_type": None}
    with pytest.raises(IntentParserInvalidOutput):
        decode_intent({"intent":"query_context","arguments":{"query_type":"weather","destination":"Воронеж","transport_type":None}})
    with pytest.raises(IntentParserInvalidOutput):
        decode_intent({"intent":"query_context","arguments":{"query_type":"arrival","destination":"","transport_type":None}})


PRODUCTION_CONTEXT_CASES = [
    ("Во сколько поезд в Воронеж?", "departure", "Воронеж", "train"),
    ("Когда приезжаем в Воронеж?", "arrival", "Воронеж", None),
    ("Когда обратный поезд?", "return", None, "train"),
    ("Когда мы возвращаемся из Воронежа?", "return", "Воронеж", None),
    ("Что известно про поездку в Воронеж?", "overview", "Воронеж", None),
    ("Какие документы есть на поездку в Воронеж?", "documents", "Воронеж", None),
]


@pytest.mark.parametrize(("text", "query_type", "destination", "transport_type"), PRODUCTION_CONTEXT_CASES)
def test_production_context_provider_envelopes_decode(text, query_type, destination, transport_type):
    """Exercise the compact Polza envelope that precedes Context Engine resolution."""
    arguments = [{"name": "query_type", "value": query_type}]
    if destination is not None:
        arguments.append({"name": "destination", "value": destination})
    if transport_type is not None:
        arguments.append({"name": "transport_type", "value": transport_type})

    parsed = decode_provider_envelope({"intent": "query_context", "arguments": arguments})

    assert text  # documents which production phrase the controlled fixture represents
    assert parsed.intent is IntentKind.QUERY_CONTEXT
    assert parsed.arguments == {
        "query_type": query_type, "destination": destination, "transport_type": transport_type,
    }


@pytest.mark.parametrize("transport_type", ["train", "plane", "bus", "other"])
def test_context_provider_transport_vocabulary_is_accepted(transport_type):
    parsed = decode_provider_envelope({"intent": "query_context", "arguments": [
        {"name": "query_type", "value": "departure"},
        {"name": "transport_type", "value": transport_type},
    ]})
    assert parsed.arguments["transport_type"] == transport_type


def test_production_russian_transport_value_remains_rejected():
    raw_provider_envelope = {"intent": "query_context", "arguments": [
        {"name": "query_type", "value": "departure"},
        {"name": "destination", "value": "Воронеж"},
        {"name": "transport_type", "value": "поезд"},
    ]}
    with pytest.raises(IntentParserInvalidOutput, match="invalid_transport_type"):
        decode_provider_envelope(raw_provider_envelope)


def test_send_ticket_routing_provider_envelope_remains_attachment_retrieval():
    parsed = decode_provider_envelope({"intent": "query_event_attachments", "arguments": [
        {"name": "semantic_type", "value": "transport_ticket"},
        {"name": "destination", "value": "Воронеж"},
    ]})
    assert parsed.intent is IntentKind.QUERY_EVENT_ATTACHMENTS
    assert parsed.arguments["destination"] == "Воронеж"
