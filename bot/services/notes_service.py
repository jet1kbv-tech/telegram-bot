from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from bot.storage import (MAX_NOTE_TEXT_LENGTH, NOTE_OWNERS, NOTE_SOURCES, JsonStorage,
                         make_id, normalize_note)


class NotesValidationError(ValueError):
    """Raised when a caller supplies an invalid Notes domain value."""


class NotesService:
    """Actor-scoped Notes operations, independent of Telegram objects."""

    def __init__(self, storage: JsonStorage, *, clock: Callable[[], datetime] | None = None):
        self.storage = storage
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _owner(owner: str) -> str:
        value = str(owner or "").strip().lower()
        if value not in NOTE_OWNERS:
            raise NotesValidationError("invalid owner")
        return value

    @staticmethod
    def _text(text: str) -> str:
        value = str(text or "").strip()
        if not value:
            raise NotesValidationError("empty text")
        if len(value) > MAX_NOTE_TEXT_LENGTH:
            raise NotesValidationError("text too long")
        return value

    def create_note(self, *, owner: str, text: str, source: str = "manual") -> dict[str, str]:
        owner, text = self._owner(owner), self._text(text)
        if source not in NOTE_SOURCES:
            raise NotesValidationError("unsupported source")
        now = self._clock().astimezone(timezone.utc).isoformat()
        note = normalize_note({"id": make_id(), "owner": owner, "text": text,
            "created_at": now, "updated_at": now, "source": source})
        assert note is not None
        self.storage.update(lambda data: data.setdefault("notes", []).append(note))
        return dict(note)

    def list_notes(self, *, owner: str) -> list[dict[str, str]]:
        owner = self._owner(owner)
        notes = [dict(note) for note in self.storage.load().get("notes", [])
                 if note.get("owner") == owner]
        return sorted(notes, key=lambda note: (note.get("updated_at", ""), note.get("id", "")), reverse=True)

    def get_note(self, *, owner: str, note_id: str) -> dict[str, str] | None:
        owner = self._owner(owner)
        if not isinstance(note_id, str) or not note_id:
            return None
        return next((dict(note) for note in self.storage.load().get("notes", [])
                     if note.get("owner") == owner and note.get("id") == note_id), None)

    def update_note(self, *, owner: str, note_id: str, text: str) -> dict[str, str] | None:
        owner, text = self._owner(owner), self._text(text)
        if not isinstance(note_id, str) or not note_id:
            return None

        def mutate(data):
            note = next((item for item in data.get("notes", [])
                         if item.get("owner") == owner and item.get("id") == note_id), None)
            if note is None:
                return None
            now = self._clock().astimezone(timezone.utc)
            try:
                previous = datetime.fromisoformat(note["updated_at"].replace("Z", "+00:00"))
                if previous.tzinfo is None:
                    previous = previous.replace(tzinfo=timezone.utc)
                if now <= previous:
                    now = previous + timedelta(microseconds=1)
            except (KeyError, TypeError, ValueError):
                pass
            note["text"] = text
            note["updated_at"] = now.isoformat()
            return dict(note)

        result, _ = self.storage.update(mutate)
        return result

    def delete_note(self, *, owner: str, note_id: str) -> bool:
        owner = self._owner(owner)
        if not isinstance(note_id, str) or not note_id:
            return False

        def mutate(data):
            notes = data.get("notes", [])
            note = next((item for item in notes
                         if item.get("owner") == owner and item.get("id") == note_id), None)
            if note is None:
                return False
            notes.remove(note)
            return True

        deleted, _ = self.storage.update(mutate)
        return deleted
