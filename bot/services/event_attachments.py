"""Domain operations for files owned by calendar and Afisha events.

Afisha calendar projections resolve to, and never copy files from, their source
Afisha event.  Functions in this module deliberately contain no Telegram API calls.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bot.storage import find_item, make_id, normalize_event_attachment

EDITABLE_METADATA = {"origin", "destination", "date", "departure_time", "person"}


class AttachmentParentNotFound(ValueError):
    pass


def resolve_attachment_parent(data: dict[str, Any], parent_type: str, parent_event_id: str) -> tuple[str, str, dict[str, Any]]:
    if parent_type == "afisha":
        event = find_item(data.get("afisha", []), parent_event_id)
        if event:
            return "afisha", parent_event_id, event
    elif parent_type == "calendar":
        for items in data.get("calendars", {}).values():
            event = find_item(items, parent_event_id)
            if not event:
                continue
            if event.get("source") == "afisha" and event.get("source_id"):
                source_id = str(event["source_id"])
                source = find_item(data.get("afisha", []), source_id)
                if source:
                    return "afisha", source_id, source
                break
            return "calendar", parent_event_id, event
    raise AttachmentParentNotFound("Event attachment parent does not exist")


def create_event_attachment(data: dict[str, Any], *, parent_type: str, parent_event_id: str,
                            telegram_file_id: str, telegram_media_type: str,
                            telegram_file_unique_id: str = "", semantic_type: str = "other",
                            created_by: str = "unknown", **metadata: Any) -> tuple[dict[str, Any], bool]:
    resolved_type, resolved_id, _ = resolve_attachment_parent(data, parent_type, parent_event_id)
    attachments = data.setdefault("event_attachments", [])
    unique_id = str(telegram_file_unique_id or "")
    if unique_id:
        existing = next((item for item in attachments if item.get("parent_type") == resolved_type
                         and item.get("parent_event_id") == resolved_id
                         and item.get("telegram_file_unique_id") == unique_id), None)
        if existing:
            return existing, False
    candidate = normalize_event_attachment({
        "id": make_id(), "parent_type": resolved_type, "parent_event_id": resolved_id,
        "telegram_file_id": telegram_file_id, "telegram_file_unique_id": unique_id,
        "telegram_media_type": telegram_media_type, "semantic_type": semantic_type,
        "created_by": created_by,
        "created_at": datetime.now(timezone.utc).isoformat(), **metadata,
    })
    if candidate is None:
        raise ValueError("Invalid event attachment")
    while any(item.get("id") == candidate["id"] for item in attachments):
        candidate["id"] = make_id()
    attachments.append(candidate)
    return candidate, True


def get_event_attachment(data: dict[str, Any], attachment_id: str) -> dict[str, Any] | None:
    return find_item(data.get("event_attachments", []), attachment_id)


def list_event_attachments(data: dict[str, Any]) -> list[dict[str, Any]]:
    return list(data.get("event_attachments", []))


def get_attachments_for_event(data: dict[str, Any], parent_type: str, parent_event_id: str) -> list[dict[str, Any]]:
    resolved_type, resolved_id, _ = resolve_attachment_parent(data, parent_type, parent_event_id)
    return [item for item in data.get("event_attachments", [])
            if item.get("parent_type") == resolved_type and item.get("parent_event_id") == resolved_id]


def delete_event_attachment(data: dict[str, Any], attachment_id: str) -> bool:
    items = data.setdefault("event_attachments", [])
    for index, item in enumerate(items):
        if item.get("id") == attachment_id:
            del items[index]
            return True
    return False


def update_event_attachment_metadata(data: dict[str, Any], attachment_id: str, **changes: Any) -> dict[str, Any]:
    """Validate and update the metadata allow-list without touching ownership/file fields."""
    if not changes or not set(changes) <= EDITABLE_METADATA:
        raise ValueError("Unsupported attachment metadata")
    item = get_event_attachment(data, attachment_id)
    if item is None:
        raise ValueError("Event attachment does not exist")
    candidate = normalize_event_attachment({**item, **changes})
    if candidate is None:
        raise ValueError("Invalid attachment metadata")
    for field in ("date", "departure_time"):
        supplied = changes.get(field)
        if supplied not in {None, ""} and candidate[field] is None:
            raise ValueError(f"Invalid {field}")
    item.clear(); item.update(candidate)
    return item


def delete_attachments_for_event(data: dict[str, Any], parent_type: str, parent_event_id: str) -> int:
    resolved_type, resolved_id, _ = resolve_attachment_parent(data, parent_type, parent_event_id)
    before = len(data.setdefault("event_attachments", []))
    data["event_attachments"] = [item for item in data["event_attachments"]
                                 if not (item.get("parent_type") == resolved_type and item.get("parent_event_id") == resolved_id)]
    return before - len(data["event_attachments"])
