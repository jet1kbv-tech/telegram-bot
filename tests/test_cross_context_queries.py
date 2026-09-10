from copy import deepcopy
from datetime import datetime, timezone

import pytest

from bot.services.context_queries import execute_context_query, query_context
from bot.services.context_sessions import get_context_session
from bot.services.nl_intent import IntentParserInvalidOutput
from bot.services.nl_intent_decoder import decode_provider_envelope

NOW = datetime(2026, 9, 10, 9, tzinfo=timezone.utc)


def event(identity, title, day, clock="10:00", **extra):
    return {"id": identity, "title": title, "date": day, "time": clock,
            "status": "active", **extra}


def manual(identity, title, day, clock="10:00", **extra):
    return {"id": identity, "title": title, "date": day, "start_time": clock,
            "source": "manual", **extra}


def document(identity, parent, semantic_type="other", **extra):
    return {"id": identity, "parent_type": extra.pop("parent_type", "afisha"),
            "parent_event_id": parent, "semantic_type": semantic_type,
            "telegram_media_type": "document", **extra}


def trip_tickets(parent="trip", *, arrival=True, returning=True):
    outbound = document("out", parent, "transport_ticket", transport_type="train",
        origin="Москва", destination="Санкт-Петербург", date="2026-09-12",
        departure_time="08:00", arrival_date="2026-09-12" if arrival else None,
        arrival_time="12:00" if arrival else None)
    rows = [outbound]
    if returning:
        rows.append(document("back", parent, "transport_ticket", transport_type="train",
            origin="Санкт-Петербург", destination="Москва", date="2026-09-15",
            departure_time="18:00", arrival_date="2026-09-15", arrival_time="22:00"))
    return rows


def snapshot(*, afisha=(), vova=(), sasha=(), documents=()):
    return {"afisha": list(afisha), "calendars": {"vova": list(vova), "sasha": list(sasha)},
            "event_attachments": list(documents), "meta": {"context_sessions": {}}}


def ask(data, query_type, **kwargs):
    return query_context(data, actor_key=kwargs.pop("actor", "vova"), now=NOW,
                         timezone="Europe/Moscow", query_type=query_type, **kwargs)


@pytest.mark.parametrize(("start", "end"), [
    ("2026-09-13", None), ("2026-09-11", "2026-09-13"),
    ("2026-09-14", "2026-09-16"), ("2026-09-12", "2026-09-12"),
])
def test_explicit_interval_overlap_shapes(start, end):
    plan = event("plan", "План", start, "13:00")
    if end:
        plan.update(end_date=end, end_time="14:00")
    data = snapshot(afisha=[event("trip", "Поездка", "2026-09-12"), plan],
                    documents=trip_tickets())
    result = ask(data, "events_during_trip", destination="Питер")
    assert result.outcome == "found" and "План" in result.text and "Поездка" not in result.text


def test_overlap_excludes_outside_projection_and_orders_chronologically():
    afisha = [event("trip", "Поездка", "2026-09-12"), event("late", "Позже", "2026-09-14"),
              event("early", "Раньше", "2026-09-13"), event("outside", "Мимо", "2026-09-16")]
    projection = {"id": "projection", "title": "Раньше", "date": "2026-09-13", "start_time": "10:00",
                  "source": "afisha", "source_id": "early"}
    result = ask(snapshot(afisha=afisha, vova=[projection], documents=trip_tickets()),
                 "events_during_trip", destination="Санкт-Петербург")
    assert "Мимо" not in result.text and result.text.count("Раньше") == 1
    assert result.text.index("Раньше") < result.text.index("Позже")


def test_trip_resolution_ambiguity_not_found_and_missing_end():
    first = event("one", "Питер 1", "2026-09-12")
    second = event("two", "Питер 2", "2026-10-12")
    docs = trip_tickets("one", returning=False) + [document("out2", "two", "transport_ticket",
        origin="Москва", destination="Санкт-Петербург", date="2026-10-12")]
    assert ask(snapshot(afisha=[first, second], documents=docs), "events_during_trip",
               destination="Питер").outcome == "ambiguous"
    assert ask(snapshot(afisha=[first], documents=trip_tickets("one")), "events_during_trip",
               destination="Казань").outcome == "not_found"
    missing = snapshot(afisha=[event("one", "Питер 1", "2026-09-12", "08:00")], documents=trip_tickets("one", arrival=False, returning=False))
    assert ask(missing, "events_during_trip", destination="Питер").outcome == "missing"


def test_arrival_day_is_explicit_and_does_not_fallback():
    plans = [event("same", "Встреча", "2026-09-12", "15:00"), event("other", "Не сегодня", "2026-09-13")]
    base = [event("trip", "Питер", "2026-09-12"), *plans]
    result = ask(snapshot(afisha=base, documents=trip_tickets()), "events_on_trip_arrival", destination="Питер")
    assert "Встреча" in result.text and "Не сегодня" not in result.text
    no_arrival = snapshot(afisha=base, documents=trip_tickets(arrival=False, returning=False))
    assert ask(no_arrival, "events_on_trip_arrival", destination="Питер").outcome == "missing"


def test_trip_document_coverage_types_and_missing_return():
    afisha = [event("trip", "Питер", "2026-09-12")]
    with_voucher = snapshot(afisha=afisha, documents=[*trip_tickets(returning=False),
        document("voucher", "trip", "voucher")])
    assert ask(with_voucher, "trips_missing_documents", semantic_type="voucher").outcome == "not_found"
    assert ask(with_voucher, "trips_missing_documents", semantic_type="insurance").outcome == "found"
    assert ask(with_voucher, "trips_missing_return").outcome == "found"
    assert ask(snapshot(afisha=afisha, documents=trip_tickets()), "trips_missing_return").outcome == "not_found"


def test_event_document_queries_type_visibility_and_person_cannot_widen():
    shared = event("shared", "Концерт", "2026-09-13")
    private = manual("private", "Секрет", "2026-09-13")
    docs = [document("shared-doc", "shared", "reservation"),
            document("private-doc", "private", "insurance", parent_type="calendar")]
    data = snapshot(afisha=[shared], sasha=[private], documents=docs)
    assert "Концерт" in ask(data, "events_with_document_type", semantic_type="reservation").text
    assert "Секрет" not in ask(data, "events_with_documents", actor="vova", person="sasha").text
    assert "Секрет" in ask(data, "events_with_documents", actor="sasha").text
    without = ask(data, "events_without_documents", actor="vova")
    assert without.outcome == "not_found"


def test_date_range_decoder_read_only_and_context_pointer_rules():
    data = snapshot(afisha=[event("trip", "Питер", "2026-09-12"), event("plan", "План", "2026-09-13")],
                    documents=trip_tickets(returning=False))
    before = deepcopy(data)
    result = execute_context_query(data, actor_key="vova", now=NOW, timezone="UTC",
        query_type="events_during_trip", destination="Питер", semantic_type=None)
    assert result.trip and get_context_session(data, "vova", NOW).domain == "trip"
    assert {key: value for key, value in data.items() if key != "meta"} == {key: value for key, value in before.items() if key != "meta"}
    data["meta"]["context_sessions"].clear()
    execute_context_query(data, actor_key="vova", now=NOW, timezone="UTC",
        query_type="trips_missing_documents", semantic_type="insurance")
    assert get_context_session(data, "vova", NOW) is None
    parsed = decode_provider_envelope({"intent": "query_context", "arguments": [
        {"name": "query_type", "value": "events_with_document_type"},
        {"name": "semantic_type", "value": "reservation"}]})
    assert parsed.arguments["semantic_type"] == "reservation"
    with pytest.raises(IntentParserInvalidOutput, match="invalid_semantic_type"):
        decode_provider_envelope({"intent": "query_context", "arguments": [
            {"name": "query_type", "value": "events_with_document_type"},
            {"name": "semantic_type", "value": "passport"}]})
