from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from bot.services.context_queries import execute_context_query
from bot.services.context_sessions import CONTEXT_SESSION_TTL, get_context_session
from bot.services.nl_intent import IntentKind
from bot.services.nl_intent_decoder import decode_provider_envelope
from bot.storage import JsonStorage

NOW = datetime(2026, 9, 9, 9, tzinfo=timezone.utc)


def snapshot(*, place="VK Stadium", second_concert=False):
    afisha = [
        {"id": "concert", "title": "Концерт", "date": "2026-09-14", "time": "19:30",
         "place": place, "status": "active"},
        {"id": "trip", "title": "Питер", "date": "2026-09-12", "time": "08:40", "status": "active"},
    ]
    if second_concert:
        afisha.append({"id": "concert2", "title": "Концерт", "date": "2026-09-20", "time": "20:00", "status": "active"})
    attachments = [
        {"id": "out", "parent_type": "afisha", "parent_event_id": "trip", "semantic_type": "transport_ticket",
         "transport_type": "train", "origin": "Москва", "destination": "Санкт-Петербург", "date": "2026-09-12",
         "departure_time": "08:40", "arrival_date": "2026-09-12", "arrival_time": "12:30", "telegram_media_type": "document"},
        {"id": "back", "parent_type": "afisha", "parent_event_id": "trip", "semantic_type": "transport_ticket",
         "transport_type": "train", "origin": "Санкт-Петербург", "destination": "Москва", "date": "2026-09-15",
         "departure_time": "18:30", "arrival_date": "2026-09-15", "arrival_time": "22:20", "telegram_media_type": "document"},
        {"id": "event-ticket", "parent_type": "afisha", "parent_event_id": "concert", "semantic_type": "other",
         "telegram_file_id": "SECRET", "telegram_media_type": "document"},
    ]
    return {"calendars": {"vova": [], "sasha": []}, "afisha": afisha,
            "event_attachments": attachments, "meta": {"context_sessions": {}}}


def ask(data, actor="vova", follow_up=False, **arguments):
    return execute_context_query(data, actor_key=actor, now=NOW, timezone="Europe/Moscow",
                                 follow_up=follow_up, **arguments)


def test_event_session_follow_ups_and_missing_field_still_establishes_subject():
    data = snapshot(place="")
    first = ask(data, query_type="event_place", target="концерт")
    assert first.outcome == "missing" and get_context_session(data, "vova", NOW).domain == "event"
    assert "19:30" in ask(data, follow_up=True, query_type="event_time").text
    assert "14 сентября" in ask(data, follow_up=True, query_type="event_date").text
    documents = ask(data, follow_up=True, query_type="documents").text
    assert "документ" in documents and "SECRET" not in documents and "event-ticket" not in documents


@pytest.mark.parametrize(("query_type", "expected"), [
    ("return", "18:30"), ("arrival", "12:30"), ("documents", "билет на поезд"),
    ("origin", "Москва"), ("destination", "Санкт-Петербург"), ("event_time", "08:40"),
])
def test_trip_follow_ups(query_type, expected):
    data = snapshot()
    ask(data, query_type="departure", destination="Санкт-Петербург")
    assert expected in ask(data, follow_up=True, query_type=query_type).text


def test_new_success_replaces_but_failed_and_ambiguous_queries_preserve():
    data = snapshot(second_concert=True)
    ask(data, query_type="departure", destination="Санкт-Петербург")
    trip_id = get_context_session(data, "vova", NOW).context_id
    ask(data, query_type="event_time", target="несуществующее")
    ask(data, query_type="event_time", target="концерт")
    assert get_context_session(data, "vova", NOW).context_id == trip_id
    data["afisha"].pop()  # make the event self-contained and unambiguous
    ask(data, query_type="event_time", target="концерт")
    assert get_context_session(data, "vova", NOW).domain == "event"


def test_actor_isolation_provider_person_and_shared_afisha_sessions():
    data = snapshot()
    ask(data, actor="vova", query_type="event_time", target="концерт", person="sasha")
    assert ask(data, actor="sasha", follow_up=True, query_type="event_place").outcome == "missing_context"
    ask(data, actor="sasha", query_type="event_time", target="концерт", person="vova")
    assert set(data["meta"]["context_sessions"]) == {"vova", "sasha"}


def test_ttl_expiration_is_removed_and_not_resurrected():
    data = snapshot()
    ask(data, query_type="event_time", target="концерт")
    expired_now = NOW + CONTEXT_SESSION_TTL + timedelta(seconds=1)
    first = execute_context_query(data, actor_key="vova", now=expired_now, timezone="UTC",
                                  follow_up=True, query_type="event_place")
    second = execute_context_query(data, actor_key="vova", now=expired_now, timezone="UTC",
                                   follow_up=True, query_type="event_place")
    assert first.outcome == second.outcome == "missing_context"
    assert "vova" not in data["meta"]["context_sessions"]


def test_compatibility_no_context_and_corrupt_context_fail_closed():
    data = snapshot()
    assert ask(data, follow_up=True, query_type="return").outcome == "missing_context"
    ask(data, query_type="event_time", target="концерт")
    assert ask(data, follow_up=True, query_type="return").outcome == "missing_context"
    data["meta"]["context_sessions"]["vova"] = {"domain": "trip", "context_id": "x"}
    assert ask(data, follow_up=True, query_type="departure").outcome == "missing_context"


def test_fresh_event_edit_and_delete_are_revalidated_without_mutation():
    data = snapshot()
    ask(data, query_type="event_place", target="концерт")
    data["afisha"][0]["place"] = "Новая площадка"
    before = deepcopy(data["afisha"])
    assert "Новая площадка" in ask(data, follow_up=True, query_type="event_place").text
    assert data["afisha"] == before
    data["afisha"][0]["status"] = "done"
    assert ask(data, follow_up=True, query_type="event_place").outcome == "unavailable"
    assert "vova" not in data["meta"]["context_sessions"]


def test_fresh_trip_fields_and_lost_visibility_are_revalidated():
    data = snapshot()
    ask(data, query_type="departure", destination="Санкт-Петербург")
    data["event_attachments"][0]["departure_time"] = "09:10"
    assert "09:10" in ask(data, follow_up=True, query_type="departure").text
    data["afisha"][1]["status"] = "done"
    assert ask(data, follow_up=True, query_type="departure").outcome == "unavailable"


def test_forged_other_actor_private_reference_does_not_grant_visibility():
    data = snapshot()
    data["afisha"] = []
    data["calendars"]["sasha"] = [
        {"id": "private", "title": "Секретная поездка", "date": "2026-09-12", "start_time": "08:40", "source": "manual"}]
    for item in data["event_attachments"][:2]:
        item["parent_type"] = "calendar"
        item["parent_event_id"] = "private"
    ask(data, actor="sasha", query_type="departure", destination="Санкт-Петербург")
    data["meta"]["context_sessions"]["vova"] = deepcopy(data["meta"]["context_sessions"]["sasha"])
    result = ask(data, actor="vova", follow_up=True, query_type="departure")
    assert result.outcome == "unavailable" and "08:40" not in result.text


def test_old_storage_restart_and_independent_atomic_actor_updates(tmp_path):
    store = JsonStorage(tmp_path / "data.json")
    store.save(snapshot())
    store.update(lambda data: execute_context_query(data, actor_key="vova", now=NOW, timezone="UTC",
                                                    query_type="event_time", target="концерт"))
    store.update(lambda data: execute_context_query(data, actor_key="sasha", now=NOW, timezone="UTC",
                                                    query_type="event_time", target="концерт"))
    assert set(store.load()["meta"]["context_sessions"]) == {"vova", "sasha"}
    old = store.default_data()
    old["meta"].pop("context_sessions")
    store.save(old)
    assert store.load()["meta"]["context_sessions"] == {}


def test_follow_up_decoder_is_explicit_and_mutation_fragment_stays_unsupported():
    parsed = decode_provider_envelope({"intent": "query_context", "arguments": [
        {"name": "query_type", "value": "return"}, {"name": "follow_up", "value": "true"}]})
    assert parsed.intent is IntentKind.QUERY_CONTEXT and parsed.arguments["follow_up"] is True
    unsupported = decode_provider_envelope({"intent": "unsupported", "arguments": [
        {"name": "category", "value": "conversation"}]})
    assert unsupported.intent is IntentKind.UNSUPPORTED
