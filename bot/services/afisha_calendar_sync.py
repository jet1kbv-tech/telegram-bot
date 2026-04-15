from __future__ import annotations

from typing import Any

from bot.storage import normalize_calendar_event, sort_calendar_events

CALENDAR_OWNERS = ("vova", "sasha")


def build_afisha_projection_id(source_id: str, owner: str) -> str:
    return f"cal_afisha_{source_id}_{owner}"


def _build_projection_comment(afisha_event: dict[str, Any]) -> str:
    lines: list[str] = []
    if afisha_event.get("place"):
        lines.append(f"Где: {afisha_event['place']}")
    if afisha_event.get("link"):
        lines.append(f"Ссылка: {afisha_event['link']}")
    return "\n".join(lines)


def _projection_end_time(afisha_event: dict[str, Any]) -> str:
    end_date = str(afisha_event.get("end_date") or "")
    end_time = str(afisha_event.get("end_time") or "")
    date = str(afisha_event.get("date") or "")
    if end_time and (not end_date or end_date == date):
        return end_time
    return ""


def project_afisha_to_calendars(data: dict[str, Any], afisha_event: dict[str, Any]) -> None:
    source_id = str(afisha_event.get("id") or "").strip()
    if not source_id:
        return

    calendars = data.setdefault("calendars", {})
    for owner in CALENDAR_OWNERS:
        items = calendars.setdefault(owner, [])
        projection_id = build_afisha_projection_id(source_id, owner)
        projection = {
            "id": projection_id,
            "owner": owner,
            "title": str(afisha_event.get("title") or "Без названия"),
            "date": str(afisha_event.get("date") or ""),
            "start_time": str(afisha_event.get("time") or ""),
            "end_time": _projection_end_time(afisha_event),
            "comment": _build_projection_comment(afisha_event),
            "notified_24h": False,
            "source": "afisha",
            "source_id": source_id,
        }
        normalized_projection = normalize_calendar_event(projection, owner)
        if normalized_projection is None:
            continue

        existing = next((item for item in items if item.get("id") == projection_id), None)
        if existing:
            normalized_projection["notified_24h"] = bool(existing.get("notified_24h", False))
            existing.clear()
            existing.update(normalized_projection)
        else:
            items.append(normalized_projection)

        calendars[owner] = sort_calendar_events(items)


def remove_afisha_from_calendars(data: dict[str, Any], source_id: str) -> None:
    source_id = str(source_id or "").strip()
    if not source_id:
        return

    calendars = data.setdefault("calendars", {})
    for owner in CALENDAR_OWNERS:
        items = calendars.setdefault(owner, [])
        projection_id = build_afisha_projection_id(source_id, owner)
        calendars[owner] = [
            item
            for item in items
            if not (
                item.get("id") == projection_id
                or (item.get("source") == "afisha" and item.get("source_id") == source_id)
            )
        ]
