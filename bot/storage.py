import json
import logging
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from bot.config import (
    AFISHA_STATUSES,
    BACKLOG_STATUSES,
    FILM_STATUSES,
    KNOWN_WISHLIST_OWNERS,
    LEISURE_STATUSES,
    WISHLIST_STATUSES,
)

logger = logging.getLogger(__name__)
DATA_FILE = Path(os.getenv("DATA_FILE", "data.json"))


class JsonStorage:
    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()

    def default_data(self) -> dict[str, Any]:
        return {
            "films": [],
            "wishlist": [],
            "leisure": [],
            "afisha": [],
            "backlog": [],
            "tickets": {
                "active": [],
                "used": [],
            },
            "spark": {
                "active": [],
                "done": [],
            },
            "places": {
                "moscow": {
                    "active": [],
                    "visited": [],
                },
                "cities": [],
            },
            "calendars": {
                "vova": [],
                "sasha": [],
            },
            "meta": {
                "user_chats": {},
            },
        }

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return self.default_data()

            try:
                with self.path.open("r", encoding="utf-8") as file:
                    raw_data = json.load(file)
            except (json.JSONDecodeError, OSError):
                logger.exception("Не удалось прочитать %s, использую пустую структуру", self.path)
                return self.default_data()

            return self._normalize_data(raw_data)

    def save(self, data: dict[str, Any]) -> None:
        normalized = self._normalize_data(data)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self.path.parent),
                delete=False,
            ) as temp_file:
                json.dump(normalized, temp_file, ensure_ascii=False, indent=2)
                temp_name = temp_file.name
            os.replace(temp_name, self.path)

    def update(self, mutator):
        with self._lock:
            data = self.load()
            result = mutator(data)
            self.save(data)
            return result, data

    def _normalize_data(self, raw_data: Any) -> dict[str, Any]:
        data = self.default_data()
        if not isinstance(raw_data, dict):
            return data

        for raw_item in raw_data.get("films", []):
            item = normalize_film(raw_item)
            if item:
                data["films"].append(item)

        for raw_item in raw_data.get("wishlist", []):
            item = normalize_wishlist(raw_item)
            if item:
                data["wishlist"].append(item)

        for raw_item in raw_data.get("leisure", []):
            item = normalize_leisure(raw_item)
            if item:
                data["leisure"].append(item)

        for raw_item in raw_data.get("afisha", []):
            item = normalize_event(raw_item)
            if item:
                data["afisha"].append(item)

        for raw_item in raw_data.get("backlog", []):
            item = normalize_backlog_item(raw_item)
            if item:
                data["backlog"].append(item)

        normalize_tickets_root(data, raw_data.get("tickets"))
        normalize_spark_root(data, raw_data.get("spark"))
        normalize_places_root(data, raw_data.get("places"))

        raw_calendars = raw_data.get("calendars") if isinstance(raw_data.get("calendars"), dict) else {}
        for owner in ("vova", "sasha"):
            for raw_item in raw_calendars.get(owner, []):
                item = normalize_calendar_event(raw_item, owner)
                if item:
                    data["calendars"][owner].append(item)

        meta = raw_data.get("meta") if isinstance(raw_data.get("meta"), dict) else {}
        user_chats = meta.get("user_chats") if isinstance(meta.get("user_chats"), dict) else {}
        data["meta"]["user_chats"] = {
            str(username): chat_id
            for username, chat_id in user_chats.items()
            if isinstance(username, str) and isinstance(chat_id, int)
        }
        return data


storage = JsonStorage(DATA_FILE)


def make_id() -> str:
    return uuid.uuid4().hex[:8]


def normalize_rating(value: Any) -> int | None:
    if value in (None, "", "-"):
        return None
    try:
        rating = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= rating <= 10:
        return rating
    return None


def calculate_average_rating(item: dict[str, Any]) -> float | None:
    ratings = [value for value in [item.get("sasha_rating"), item.get("vova_rating")] if isinstance(value, int)]
    if len(ratings) == 2:
        return sum(ratings) / 2
    legacy_rating = item.get("legacy_rating")
    if isinstance(legacy_rating, int):
        return float(legacy_rating)
    return None


def format_average_rating(item: dict[str, Any]) -> str | None:
    average = calculate_average_rating(item)
    if average is None:
        return None
    if average.is_integer():
        return str(int(average))
    return f"{average:.1f}"


def parse_event_dt(item: dict[str, Any]) -> datetime | None:
    date_raw = item.get("date")
    time_raw = item.get("time")
    if not date_raw or not time_raw:
        return None
    try:
        return datetime.strptime(f"{date_raw} {time_raw}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def parse_event_end_dt(item: dict[str, Any]) -> datetime | None:
    end_date_raw = item.get("end_date") or item.get("date")
    end_time_raw = item.get("end_time") or item.get("time")
    if not end_date_raw or not end_time_raw:
        return None
    try:
        return datetime.strptime(f"{end_date_raw} {end_time_raw}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def event_effective_end_dt(item: dict[str, Any]) -> datetime | None:
    return parse_event_end_dt(item) or parse_event_dt(item)


def format_event_dt(item: dict[str, Any]) -> str:
    start_dt = parse_event_dt(item)
    if not start_dt:
        return "Дата не указана"
    end_dt = parse_event_end_dt(item)
    if end_dt and end_dt > start_dt:
        if start_dt.date() == end_dt.date():
            return f"{start_dt.strftime('%d.%m.%Y %H:%M')} – {end_dt.strftime('%H:%M')}"
        return f"{start_dt.strftime('%d.%m.%Y %H:%M')} – {end_dt.strftime('%d.%m.%Y %H:%M')}"
    return start_dt.strftime("%d.%m.%Y %H:%M")


def is_event_actual(item: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now()
    event_end_dt = event_effective_end_dt(item)
    if not event_end_dt:
        return False
    return item.get("status") == "active" and event_end_dt >= now


def sort_events(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: parse_event_dt(item) or datetime.max)


def parse_calendar_event_start_dt(item: dict[str, Any]) -> datetime | None:
    date_raw = item.get("date")
    start_time_raw = item.get("start_time")
    if not date_raw or not start_time_raw:
        return None
    try:
        return datetime.strptime(f"{date_raw} {start_time_raw}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def parse_calendar_event_end_dt(item: dict[str, Any]) -> datetime | None:
    date_raw = item.get("date")
    start_dt = parse_calendar_event_start_dt(item)
    end_time_raw = item.get("end_time") or ""
    if end_time_raw and date_raw:
        try:
            end_dt = datetime.strptime(f"{date_raw} {end_time_raw}", "%Y-%m-%d %H:%M")
            if start_dt and end_dt > start_dt:
                return end_dt
        except ValueError:
            return None
    return start_dt


def is_calendar_event_actual(item: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now()
    end_dt = parse_calendar_event_end_dt(item)
    if not end_dt:
        return False
    return end_dt >= now


def sort_calendar_events(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: parse_calendar_event_start_dt(item) or datetime.max)


def format_calendar_event_range(item: dict[str, Any]) -> str:
    start_dt = parse_calendar_event_start_dt(item)
    if not start_dt:
        return "Дата не указана"
    end_time = item.get("end_time") or ""
    if end_time:
        return f"{start_dt.strftime('%d.%m.%Y')} {item['start_time']}–{end_time}"
    return start_dt.strftime("%d.%m.%Y %H:%M")


def calendar_preview_text(item: dict[str, Any]) -> str:
    start_dt = parse_calendar_event_start_dt(item)
    if not start_dt:
        return item.get("title") or "Событие"
    return f"{start_dt.strftime('%d.%m.%Y %H:%M')} · {item['title']}"


def get_calendar_items(data: dict[str, Any], owner: str, include_past: bool = False) -> list[dict[str, Any]]:
    raw = data.get("calendars", {}).get(owner, [])
    items = list(raw)
    if not include_past:
        now = datetime.now()
        items = [item for item in items if is_calendar_event_actual(item, now)]
    return sort_calendar_events(items)


def find_item(items: list[dict[str, Any]], item_id: str) -> dict[str, Any] | None:
    for item in items:
        if item.get("id") == item_id:
            return item
    return None


def delete_item_by_id(items: list[dict[str, Any]], item_id: str) -> bool:
    for index, item in enumerate(items):
        if item.get("id") == item_id:
            del items[index]
            return True
    return False


def normalize_film(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        return {
            "id": make_id(),
            "title": item,
            "status": "want",
            "added_by": "unknown",
            "comment": "",
            "sasha_rating": None,
            "vova_rating": None,
            "legacy_rating": None,
        }
    if isinstance(item, dict):
        status = item.get("status", "want")
        if status not in FILM_STATUSES:
            status = "want"
        sasha_rating = normalize_rating(item.get("sasha_rating"))
        vova_rating = normalize_rating(item.get("vova_rating"))
        legacy_rating = normalize_rating(item.get("legacy_rating"))
        old_rating = normalize_rating(item.get("rating"))
        if legacy_rating is None and old_rating is not None and sasha_rating is None and vova_rating is None:
            legacy_rating = old_rating
        return {
            "id": str(item.get("id") or make_id()),
            "title": str(item.get("title") or "Без названия"),
            "status": status,
            "added_by": str(item.get("added_by") or "unknown"),
            "comment": str(item.get("comment") or ""),
            "sasha_rating": sasha_rating,
            "vova_rating": vova_rating,
            "legacy_rating": legacy_rating,
        }
    return None


def normalize_wishlist(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        return {
            "id": make_id(),
            "title": item,
            "link": "",
            "comment": "",
            "status": "active",
            "owner": "unknown",
            "reserved_by": "",
        }
    if isinstance(item, dict):
        status = item.get("status", "active")
        if status not in WISHLIST_STATUSES:
            status = "active"
        owner = item.get("owner", "unknown")
        if owner not in KNOWN_WISHLIST_OWNERS:
            owner = "unknown"
        return {
            "id": str(item.get("id") or make_id()),
            "title": str(item.get("title") or "Без названия"),
            "link": str(item.get("link") or ""),
            "comment": str(item.get("comment") or ""),
            "status": status,
            "owner": owner,
            "reserved_by": str(item.get("reserved_by") or ""),
        }
    return None


def normalize_leisure(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        return {
            "id": make_id(),
            "title": item,
            "comment": "",
            "status": "want",
        }
    if isinstance(item, dict):
        status = item.get("status", "want")
        if status not in LEISURE_STATUSES:
            status = "want"
        return {
            "id": str(item.get("id") or make_id()),
            "title": str(item.get("title") or "Без названия"),
            "comment": str(item.get("comment") or ""),
            "status": status,
        }
    return None


def normalize_event(item: Any) -> dict[str, Any] | None:
    if isinstance(item, dict):
        status = item.get("status", "active")
        if status not in AFISHA_STATUSES:
            status = "active"
        normalized = {
            "id": str(item.get("id") or make_id()),
            "title": str(item.get("title") or "Без названия"),
            "place": str(item.get("place") or ""),
            "date": str(item.get("date") or ""),
            "time": str(item.get("time") or ""),
            "end_date": str(item.get("end_date") or ""),
            "end_time": str(item.get("end_time") or ""),
            "link": str(item.get("link") or ""),
            "status": status,
            "notified_24h": bool(item.get("notified_24h", False)),
            "notified_morning": bool(item.get("notified_morning", False)),
        }
        if parse_event_dt(normalized) is None:
            return None
        start_dt = parse_event_dt(normalized)
        end_dt = parse_event_end_dt(normalized)
        if normalized["end_date"] or normalized["end_time"]:
            if not normalized["end_date"]:
                normalized["end_date"] = normalized["date"]
            if not normalized["end_time"]:
                normalized["end_time"] = normalized["time"]
            end_dt = parse_event_end_dt(normalized)
            if not end_dt or (start_dt and end_dt < start_dt):
                return None
        return normalized
    return None


def normalize_backlog_item(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        return {
            "id": make_id(),
            "title": item,
            "description": "",
            "status": "todo",
        }
    if isinstance(item, dict):
        status = item.get("status", "todo")
        if status not in BACKLOG_STATUSES:
            status = "todo"
        return {
            "id": str(item.get("id") or make_id()),
            "title": str(item.get("title") or "Без названия"),
            "description": str(item.get("description") or ""),
            "status": status,
        }
    return None


def normalize_ticket_attachment(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    kind = str(item.get("kind") or "").strip()
    if kind not in {"document", "photo"}:
        return None
    file_id = str(item.get("file_id") or "").strip()
    if not file_id:
        return None
    return {
        "kind": kind,
        "file_id": file_id,
        "file_name": str(item.get("file_name") or ""),
        "mime_type": str(item.get("mime_type") or ""),
    }


def normalize_ticket_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    title = str(item.get("title") or "").strip()
    date_raw = str(item.get("date") or "").strip()
    time_raw = str(item.get("time") or "").strip()
    if not title:
        return None
    try:
        datetime.strptime(f"{date_raw} {time_raw}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None

    attachments_raw = item.get("attachments") if isinstance(item.get("attachments"), list) else []
    attachments: list[dict[str, Any]] = []
    for raw_attachment in attachments_raw:
        attachment = normalize_ticket_attachment(raw_attachment)
        if attachment:
            attachments.append(attachment)

    return {
        "id": str(item.get("id") or make_id()),
        "title": title,
        "date": date_raw,
        "time": time_raw,
        "place_route": str(item.get("place_route") or ""),
        "comment": str(item.get("comment") or ""),
        "attachments": attachments,
        "afisha_id": str(item.get("afisha_id") or ""),
    }


def normalize_tickets_root(data: dict[str, Any], raw_tickets: Any) -> None:
    tickets = {
        "active": [],
        "used": [],
    }
    if isinstance(raw_tickets, dict):
        for bucket in ("active", "used"):
            raw_bucket = raw_tickets.get(bucket, [])
            if not isinstance(raw_bucket, list):
                continue
            for raw_item in raw_bucket:
                item = normalize_ticket_item(raw_item)
                if item:
                    tickets[bucket].append(item)
    data["tickets"] = tickets


def normalize_calendar_event(item: Any, owner: str | None = None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    event_owner = str(item.get("owner") or owner or "")
    if event_owner not in {"vova", "sasha"}:
        return None
    source_raw = str(item.get("source") or "").strip()
    source_id_raw = str(item.get("source_id") or "").strip()
    source = "afisha" if source_raw == "afisha" and source_id_raw else "manual"
    source_id = source_id_raw if source == "afisha" else ""

    normalized = {
        "id": str(item.get("id") or make_id()),
        "owner": event_owner,
        "title": str(item.get("title") or "Без названия"),
        "date": str(item.get("date") or ""),
        "start_time": str(item.get("start_time") or item.get("time") or ""),
        "end_time": str(item.get("end_time") or ""),
        "comment": str(item.get("comment") or ""),
        "notified_24h": bool(item.get("notified_24h", False)),
        "source": source,
        "source_id": source_id,
    }
    if parse_calendar_event_start_dt(normalized) is None:
        return None
    end_time = normalized.get("end_time") or ""
    if end_time:
        try:
            start_dt = datetime.strptime(f"{normalized['date']} {normalized['start_time']}", "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(f"{normalized['date']} {end_time}", "%Y-%m-%d %H:%M")
        except ValueError:
            return None
        if end_dt <= start_dt:
            return None
    return normalized




def normalize_spark_item(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        title = item.strip()
        if not title:
            return None
        return {
            "id": make_id(),
            "title": title,
            "description": "",
        }

    if not isinstance(item, dict):
        return None

    title = str(item.get("title") or "").strip()
    if not title:
        return None

    description = str(item.get("description") or "").strip()

    item_id = str(item.get("id") or "").strip() or make_id()
    return {
        "id": item_id,
        "title": title,
        "description": description,
    }


def normalize_spark_root(data: dict[str, Any], raw_spark: Any) -> None:
    spark = {
        "active": [],
        "done": [],
    }

    if isinstance(raw_spark, dict):
        for bucket in ("active", "done"):
            raw_bucket = raw_spark.get(bucket, [])
            if not isinstance(raw_bucket, list):
                continue
            for raw_item in raw_bucket:
                item = normalize_spark_item(raw_item)
                if item:
                    spark[bucket].append(item)

    data["spark"] = spark

def normalize_places_root(data: dict[str, Any], raw_places: Any) -> None:
    places_data = data.setdefault("places", {})
    moscow_data = places_data.setdefault("moscow", {"active": [], "visited": []})
    moscow_data.setdefault("active", [])
    moscow_data.setdefault("visited", [])
    places_data.setdefault("cities", [])

    if not isinstance(raw_places, dict):
        return

    raw_moscow = raw_places.get("moscow") if isinstance(raw_places.get("moscow"), dict) else {}
    for raw_place in raw_moscow.get("active", []):
        item = normalize_place(raw_place)
        if item:
            moscow_data["active"].append(item)
    for raw_place in raw_moscow.get("visited", []):
        item = normalize_place(raw_place)
        if item:
            moscow_data["visited"].append(item)

    raw_cities = raw_places.get("cities") if isinstance(raw_places.get("cities"), list) else []
    for raw_city in raw_cities:
        city = normalize_city(raw_city)
        if city:
            places_data["cities"].append(city)


def normalize_place(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return {
        "id": str(item.get("id") or make_id()),
        "name": str(item.get("name") or "Без названия"),
        "yandex_link": str(item.get("yandex_link")) if item.get("yandex_link") else None,
        "comment": str(item.get("comment")) if item.get("comment") else None,
    }


def normalize_visited_city_place(item: Any) -> dict[str, Any] | None:
    place = normalize_place(item)
    if place is None:
        return None
    place["visit_comment"] = str(item.get("visit_comment")) if isinstance(item, dict) and item.get("visit_comment") else None
    return place


def normalize_city(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    city = {
        "id": str(item.get("id") or make_id()),
        "name": str(item.get("name") or "Без названия"),
        "country": str(item.get("country")) if item.get("country") else None,
        "places": {
            "active": [],
            "visited": [],
        },
    }
    raw_places = item.get("places") if isinstance(item.get("places"), dict) else {}
    for raw_place in raw_places.get("active", []):
        place = normalize_place(raw_place)
        if place:
            city["places"]["active"].append(place)
    for raw_place in raw_places.get("visited", []):
        place = normalize_visited_city_place(raw_place)
        if place:
            city["places"]["visited"].append(place)
    return city
