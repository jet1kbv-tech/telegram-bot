from datetime import datetime, timezone

import pytest

from bot.services.notes_service import MAX_NOTE_TEXT_LENGTH, NotesService, NotesValidationError
from bot.storage import JsonStorage


def service(tmp_path, moments=None):
    values = iter(moments or [datetime(2026, 9, 15, 10, tzinfo=timezone.utc)])
    return NotesService(JsonStorage(tmp_path / "data.json"), clock=lambda: next(values))


def test_create_normalizes_and_source_defaults(tmp_path):
    notes = service(tmp_path)
    note = notes.create_note(owner="vova", text="  Первая\nстрока  ")
    assert note == {"id": note["id"], "owner": "vova", "text": "Первая\nстрока",
                    "created_at": "2026-09-15T10:00:00+00:00",
                    "updated_at": "2026-09-15T10:00:00+00:00", "source": "manual"}
    assert len(note["id"]) == 8


@pytest.mark.parametrize("owner", ["", "unknown", "Vasya"])
def test_invalid_owner_is_rejected(tmp_path, owner):
    with pytest.raises(NotesValidationError):
        service(tmp_path).create_note(owner=owner, text="x")


@pytest.mark.parametrize("text", ["", " \n ", "x" * (MAX_NOTE_TEXT_LENGTH + 1)])
def test_invalid_text_is_rejected_without_saving(tmp_path, text):
    notes = service(tmp_path)
    with pytest.raises(NotesValidationError):
        notes.create_note(owner="vova", text=text)
    assert notes.list_notes(owner="vova") == []


def test_actor_isolation_and_updated_order(tmp_path):
    moments = [datetime(2026, 9, 15, hour, tzinfo=timezone.utc) for hour in (10, 11, 12)]
    notes = service(tmp_path, moments)
    first = notes.create_note(owner="vova", text="first")
    second = notes.create_note(owner="vova", text="second")
    foreign = notes.create_note(owner="sasha", text="secret")
    assert [item["id"] for item in notes.list_notes(owner="vova")] == [second["id"], first["id"]]
    assert notes.get_note(owner="vova", note_id=foreign["id"]) is None
    assert notes.update_note(owner="vova", note_id=foreign["id"], text="stolen") is None
    assert notes.delete_note(owner="vova", note_id=foreign["id"]) is False
    assert notes.get_note(owner="sasha", note_id=foreign["id"])["text"] == "secret"


def test_update_preserves_identity_metadata_and_moves_note_to_top(tmp_path):
    moments = [datetime(2026, 9, 15, hour, tzinfo=timezone.utc) for hour in (10, 11, 12)]
    notes = service(tmp_path, moments)
    original = notes.create_note(owner="vova", text="old", source="universal_capture")
    other = notes.create_note(owner="vova", text="other")
    updated = notes.update_note(owner="vova", note_id=original["id"], text="new")
    for field in ("id", "owner", "created_at", "source"):
        assert updated[field] == original[field]
    assert updated["updated_at"] != original["updated_at"]
    assert updated["text"] == "new"
    assert [item["id"] for item in notes.list_notes(owner="vova")] == [original["id"], other["id"]]


def test_missing_or_malformed_ids_fail_safely(tmp_path):
    notes = service(tmp_path)
    assert notes.get_note(owner="vova", note_id="") is None
    assert notes.update_note(owner="vova", note_id="missing", text="ok") is None
    assert notes.delete_note(owner="vova", note_id="missing") is False
