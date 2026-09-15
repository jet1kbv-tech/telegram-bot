import json

from bot.storage import JsonStorage


def test_old_storage_loads_without_migration_and_preserves_other_roots(tmp_path):
    path = tmp_path / "data.json"
    legacy = JsonStorage(path).default_data()
    legacy.pop("notes")
    legacy["backlog"] = [{"id": "b1", "title": "Keep", "description": "", "status": "todo"}]
    path.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = JsonStorage(path).load()
    assert loaded["notes"] == []
    assert loaded["backlog"] == legacy["backlog"]


def test_valid_notes_round_trip_and_malformed_records_are_discarded(tmp_path):
    storage = JsonStorage(tmp_path / "data.json")
    data = storage.default_data()
    valid = {"id": "note_1", "owner": "sasha", "text": "  сохранить  ",
             "created_at": "2026-09-15T10:00:00+00:00",
             "updated_at": "2026-09-15T11:00:00+00:00", "source": "manual"}
    data["notes"] = [valid, None, "bad", {"owner": "stranger", "text": "secret"},
                     {"owner": "vova", "text": "  "}]
    storage.save(data)
    assert storage.load()["notes"] == [{**valid, "text": "сохранить"}]


def test_malformed_notes_root_does_not_break_existing_data(tmp_path):
    storage = JsonStorage(tmp_path / "data.json")
    data = storage.default_data()
    data["notes"] = {"not": "a list"}
    data["films"] = [{"id": "f1", "title": "Film", "comment": "", "status": "want"}]
    storage.save(data)
    loaded = storage.load()
    assert loaded["notes"] == []
    assert loaded["films"][0]["title"] == "Film"
