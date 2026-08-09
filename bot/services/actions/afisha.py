from __future__ import annotations

from typing import Any

from bot.services.afisha_calendar_sync import project_afisha_to_calendars
from bot.storage import make_id, normalize_event, sort_events, storage


def create_afisha_event(arguments: dict[str, Any]) -> dict[str, Any]:
    item = normalize_event({
        "id": make_id(), "title": arguments.get("title"), "place": arguments.get("place") or "",
        "date": arguments.get("date"), "time": arguments.get("time"), "end_date": arguments.get("end_date") or "",
        "end_time": arguments.get("end_time") or "", "link": arguments.get("link") or "", "status": "active",
        "notified_24h": False, "notified_morning": False,
    })
    if item is None:
        raise ValueError("invalid_afisha_event")
    def mutator(data: dict[str, Any]) -> None:
        data.setdefault("afisha", []).append(item)
        data["afisha"] = sort_events(data["afisha"])
        project_afisha_to_calendars(data, item)
    storage.update(mutator)
    return item
