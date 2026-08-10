from datetime import datetime, timezone

import pytest

from bot.services.actions import existing
from bot.services.nl_entity_resolution import normalize_reference, resolve_entities
from bot.services.nl_intent import IntentKind, IntentParserInvalidOutput
from bot.services.nl_intent_decoder import decode_intent
from bot.storage import JsonStorage


def base_data():
    data = JsonStorage.__new__(JsonStorage).default_data()
    data["purchases"]["planned"] = [{"id": "p1", "title": "Кофемашина", "price": 30000, "priority": "medium", "link": "", "comment": "", "buyer": "", "created_at": "", "bought_at": ""}]
    data["films"] = [{"id": "f1", "title": "A Beautiful Mind", "localized_title": "Игры разума", "original_title": "A Beautiful Mind", "status": "want", "comment": ""}]
    data["calendars"]["vova"] = [{"id": "c1", "owner": "vova", "title": "Стоматолог", "date": "2026-08-12", "start_time": "18:00", "end_time": "", "comment": "", "source": "manual", "source_id": "", "notified_24h": False}]
    data["calendars"]["sasha"] = [{**data["calendars"]["vova"][0], "id": "c2", "owner": "sasha"}]
    data["afisha"] = [{"id": "a1", "title": "Концерт", "date": "2026-08-15", "time": "19:00", "end_date": "", "end_time": "", "place": "", "link": "", "status": "active", "notified_24h": True, "notified_morning": True}]
    return data


def install_storage(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json")
    store.save(base_data())
    monkeypatch.setattr(existing, "storage", store)
    return store


def args(item, bucket, changes, expected=None):
    return {"_id": item["id"], "_bucket": bucket, "_changes": changes, "_expected": expected or {key: item.get(key) for key in changes}, "_actor_name": "Вова"}


def test_reference_normalization_and_resolution_is_exact():
    data = base_data()
    assert normalize_reference("  КОФЕМАШИНА!!! ") == "кофемашина"
    assert len(resolve_entities(data, IntentKind.UPDATE_PURCHASE, "кофемашина")) == 1
    assert not resolve_entities(data, IntentKind.UPDATE_PURCHASE, "кофемаш")
    assert len(resolve_entities(data, IntentKind.UPDATE_FILM, "игры разума")) == 1
    assert len(resolve_entities(data, IntentKind.UPDATE_FILM, "A Beautiful Mind")) == 1


def test_ambiguity_and_calendar_owner_isolation():
    data = base_data()
    data["calendars"]["vova"].append({**data["calendars"]["vova"][0], "id": "c3", "date": "2026-08-19"})
    assert len(resolve_entities(data, IntentKind.DELETE_CALENDAR_EVENT, "стоматолог", owner="vova")) == 2
    assert {candidate.item_id for candidate in resolve_entities(data, IntentKind.DELETE_CALENDAR_EVENT, "стоматолог", owner="sasha")} == {"c2"}


def test_calendar_mutation_resolution_filters_past_and_sorts():
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    event = {"owner": "vova", "title": "Стоматолог", "end_time": "", "source": "manual"}
    rows = [
        {**event, "id": "months", "date": "2026-02-01", "start_time": "10:00"},
        {**event, "id": "yesterday", "date": "2026-08-09", "start_time": "10:00"},
        {**event, "id": "today-past", "date": "2026-08-10", "start_time": "11:59"},
        {**event, "id": "late", "date": "2026-09-21", "start_time": "12:00"},
        {**event, "id": "today-future", "date": "2026-08-10", "start_time": "12:01"},
        {**event, "id": "all-day", "date": "2026-08-10", "start_time": ""},
        {**event, "id": "early", "date": "2026-08-15", "start_time": "16:30"},
        {**event, "id": "projection", "date": "2026-08-11", "start_time": "10:00", "source": "afisha"},
    ]
    data = {"calendars": {"vova": rows, "sasha": [{**event, "id": "other", "date": "2026-08-11", "start_time": "09:00"}]}}
    current = resolve_entities(data, IntentKind.DELETE_CALENDAR_EVENT, "стоматолог", owner="vova", now=now, timezone="UTC")
    assert [item.item_id for item in current] == ["all-day", "today-future", "early", "late"]
    historical = resolve_entities(data, IntentKind.DELETE_CALENDAR_EVENT, "стоматолог", owner="vova", include_past=True, now=now, timezone="UTC")
    assert {item.item_id for item in historical} >= {"months", "yesterday", "today-past"}
    assert "projection" not in {item.item_id for item in historical}


@pytest.mark.parametrize(("changes", "expected_bucket"), [({"priority": "high"}, "planned"), ({"price": 40000}, "planned"), ({"status": "bought"}, "bought")])
def test_purchase_updates(monkeypatch, tmp_path, changes, expected_bucket):
    store = install_storage(monkeypatch, tmp_path)
    item = store.load()["purchases"]["planned"][0]
    expected = {"status": "planned"} if "status" in changes else {key: item.get(key) for key in changes}
    assert existing.mutate_existing(IntentKind.UPDATE_PURCHASE, args(item, "planned", changes, expected)).status == "updated"
    assert store.load()["purchases"][expected_bucket][0][next(iter(changes))] == next(iter(changes.values())) if "status" not in changes else True


def test_film_comment_status_and_delete(monkeypatch, tmp_path):
    store = install_storage(monkeypatch, tmp_path); item = store.load()["films"][0]
    assert existing.mutate_existing(IntentKind.UPDATE_FILM, args(item, "films", {"status": "watched"})).status == "updated"
    current = store.load()["films"][0]
    assert existing.mutate_existing(IntentKind.UPDATE_FILM, args(current, "films", {"comment": "вместе"})).status == "updated"
    current = store.load()["films"][0]
    assert existing.mutate_existing(IntentKind.DELETE_FILM, args(current, "films", {}, current)).status == "deleted"


def test_calendar_partial_updates_preserve_other_field(monkeypatch, tmp_path):
    store = install_storage(monkeypatch, tmp_path); item = store.load()["calendars"]["vova"][0]
    existing.mutate_existing(IntentKind.UPDATE_CALENDAR_EVENT, args(item, "vova", {"date": "2026-08-14"}))
    current = store.load()["calendars"]["vova"][0]
    assert (current["date"], current["start_time"]) == ("2026-08-14", "18:00")
    existing.mutate_existing(IntentKind.UPDATE_CALENDAR_EVENT, args(current, "vova", {"start_time": "20:00"}))
    current = store.load()["calendars"]["vova"][0]
    assert (current["date"], current["start_time"]) == ("2026-08-14", "20:00")


def test_conflict_missing_and_idempotent_delete(monkeypatch, tmp_path):
    store = install_storage(monkeypatch, tmp_path); item = store.load()["films"][0]
    proposal = args(item, "films", {"status": "watched"})
    data = store.load(); data["films"][0]["status"] = "watched"; store.save(data)
    assert existing.mutate_existing(IntentKind.UPDATE_FILM, proposal).status == "conflict"
    delete = args(store.load()["films"][0], "films", {}, store.load()["films"][0])
    assert existing.mutate_existing(IntentKind.DELETE_FILM, delete).status == "deleted"
    assert existing.mutate_existing(IntentKind.DELETE_FILM, delete).status == "already_deleted"


def test_afisha_update_and_delete_sync_projections(monkeypatch, tmp_path):
    store = install_storage(monkeypatch, tmp_path); item = store.load()["afisha"][0]
    existing.mutate_existing(IntentKind.UPDATE_AFISHA_EVENT, args(item, "afisha", {"time": "20:00"}))
    data = store.load()
    assert data["afisha"][0]["time"] == "20:00"
    assert all(next(event for event in cal if event.get("source") == "afisha")["start_time"] == "20:00" for cal in data["calendars"].values())
    item = data["afisha"][0]
    assert existing.mutate_existing(IntentKind.DELETE_AFISHA_EVENT, args(item, "afisha", {}, item)).status == "deleted"
    assert all(not any(event.get("source") == "afisha" for event in cal) for cal in store.load()["calendars"].values())


def test_decoder_rejects_unexpected_and_invalid_enum():
    valid = {"intent": "update_film", "arguments": {"target": "Дюна", "status": "watched", "comment": None}}
    assert decode_intent(valid).intent is IntentKind.UPDATE_FILM
    for bad in ({**valid, "arguments": {**valid["arguments"], "id": "f1"}}, {**valid, "arguments": {**valid["arguments"], "status": "done"}}):
        with pytest.raises(IntentParserInvalidOutput): decode_intent(bad)
