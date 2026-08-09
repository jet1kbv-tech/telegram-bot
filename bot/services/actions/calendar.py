from __future__ import annotations

from typing import Any

from bot.storage import make_id, normalize_calendar_event, sort_calendar_events, storage


def create_personal_calendar_event(arguments: dict[str, Any], *, owner: str) -> dict[str, Any]:
    if owner not in {"vova", "sasha"}:
        raise ValueError("invalid_owner")
    item = normalize_calendar_event({
        "id": make_id(), "owner": owner, "title": arguments.get("title"), "date": arguments.get("date"),
        "start_time": arguments.get("start_time"), "end_time": arguments.get("end_time") or "",
        "comment": arguments.get("comment") or "", "notified_24h": False, "source": "manual", "source_id": "",
    }, owner)
    if item is None:
        raise ValueError("invalid_calendar_event")
    def mutator(data: dict[str, Any]) -> None:
        items = data.setdefault("calendars", {}).setdefault(owner, [])
        items.append(item)
        data["calendars"][owner] = sort_calendar_events(items)
    storage.update(mutator)
    return item
