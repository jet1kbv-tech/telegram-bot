from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bot.services.afisha_calendar_sync import build_afisha_projection_id, project_afisha_to_calendars, remove_afisha_from_calendars
from bot.services.nl_intent import IntentKind
from bot.storage import delete_item_by_id, find_item, normalize_calendar_event, normalize_event, normalize_purchase_item, sort_calendar_events, sort_events, storage


@dataclass(frozen=True, slots=True)
class MutationResult:
    status: str
    item: dict[str, Any] | None = None


def mutate_existing(kind: IntentKind, arguments: dict[str, Any]) -> MutationResult:
    def mutator(data: dict[str, Any]) -> MutationResult:
        item_id, bucket = arguments["_id"], arguments["_bucket"]
        if kind in {IntentKind.UPDATE_PURCHASE, IntentKind.DELETE_PURCHASE}:
            items = data.get("purchases", {}).get(bucket, [])
        elif kind in {IntentKind.UPDATE_FILM, IntentKind.DELETE_FILM}:
            items = data.get("films", [])
        elif kind in {IntentKind.UPDATE_CALENDAR_EVENT, IntentKind.DELETE_CALENDAR_EVENT}:
            items = data.get("calendars", {}).get(bucket, [])
        else:
            items = data.get("afisha", [])
        item = find_item(items, item_id)
        deleting = kind.name.startswith("DELETE_")
        if item is None:
            return MutationResult("already_deleted" if deleting else "missing")
        expected = arguments["_expected"]
        if any((bucket if field == "status" and kind is IntentKind.UPDATE_PURCHASE else item.get(field)) != old for field, old in expected.items()):
            return MutationResult("conflict")
        if deleting:
            delete_item_by_id(items, item_id)
            if kind is IntentKind.DELETE_AFISHA_EVENT:
                remove_afisha_from_calendars(data, item_id)
            return MutationResult("deleted", dict(item))
        changes = dict(arguments["_changes"])
        if kind is IntentKind.UPDATE_PURCHASE:
            target_bucket = changes.pop("status", bucket)
            if "priority" in changes and changes["priority"] == "none": changes["priority"] = ""
            if "buyer" in changes:
                changes["buyer"] = arguments.get("_actor_name", "") if changes["buyer"] == "current_user" else ""
            item.update(changes)
            normalized = normalize_purchase_item(item)
            if normalized is None: return MutationResult("invalid")
            item.clear(); item.update(normalized)
            if target_bucket != bucket:
                delete_item_by_id(items, item_id)
                if target_bucket == "bought": item["bought_at"] = datetime.now().isoformat(timespec="seconds")
                else: item["bought_at"] = ""
                data["purchases"][target_bucket].append(item)
        elif kind is IntentKind.UPDATE_FILM:
            item.update(changes)
        elif kind is IntentKind.UPDATE_CALENDAR_EVENT:
            item.update(changes)
            normalized = normalize_calendar_event(item, bucket)
            if normalized is None: return MutationResult("invalid")
            item.clear(); item.update(normalized); item["notified_24h"] = False
            data["calendars"][bucket] = sort_calendar_events(items)
        else:
            item.update(changes)
            normalized = normalize_event(item)
            if normalized is None: return MutationResult("invalid")
            item.clear(); item.update(normalized); item["notified_24h"] = False; item["notified_morning"] = False
            data["afisha"] = sort_events(items)
            remove_afisha_from_calendars(data, item_id)
            project_afisha_to_calendars(data, item)
            for owner in ("vova", "sasha"):
                projection = find_item(data.get("calendars", {}).get(owner, []), build_afisha_projection_id(item_id, owner))
                if projection:
                    projection["notified_24h"] = False
        return MutationResult("updated", dict(item))
    result, _ = storage.update(mutator)
    return result
