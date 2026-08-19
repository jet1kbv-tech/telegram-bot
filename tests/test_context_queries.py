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
