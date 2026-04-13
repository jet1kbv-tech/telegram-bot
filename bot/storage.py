import json
import os
import tempfile
import uuid
from pathlib import Path
from threading import RLock
from typing import Any

from bot.config import DATA_FILE


def make_id() -> str:
    return uuid.uuid4().hex[:8]


def normalize_moscow_place(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    status = str(raw.get("status") or "planned")
    if status not in {"planned", "visited"}:
        status = "planned"
    return {
        "id": str(raw.get("id") or make_id()),
        "title": str(raw.get("title") or "Без названия"),
        "link": str(raw.get("link") or ""),
        "comment": str(raw.get("comment") or ""),
        "status": status,
    }


def normalize_city_place(raw: Any) -> dict[str, Any] | None:
    base = normalize_moscow_place(raw)
    if not base:
        return None
    base["visited_comment"] = str(raw.get("visited_comment") or "") if isinstance(raw, dict) else ""
    return base


def normalize_city(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    places: list[dict[str, Any]] = []
    for raw_place in raw.get("places", []):
        place = normalize_city_place(raw_place)
        if place:
            places.append(place)
    return {
        "id": str(raw.get("id") or make_id()),
        "title": str(raw.get("title") or "Без названия"),
        "country": str(raw.get("country") or ""),
        "places": places,
    }


def normalize_places(raw: Any) -> dict[str, Any]:
    normalized = {"moscow": [], "cities": []}
    if not isinstance(raw, dict):
        return normalized
    for item in raw.get("moscow", []):
        place = normalize_moscow_place(item)
        if place:
            normalized["moscow"].append(place)
    for item in raw.get("cities", []):
        city = normalize_city(item)
        if city:
            normalized["cities"].append(city)
    return normalized


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
            "calendars": {"vova": [], "sasha": []},
            "meta": {"user_chats": {}},
            "places": {"moscow": [], "cities": []},
        }

    def _normalize_data(self, raw: Any) -> dict[str, Any]:
        data = self.default_data()
        if not isinstance(raw, dict):
            return data
        for key in ("films", "wishlist", "leisure", "afisha", "backlog"):
            value = raw.get(key)
            if isinstance(value, list):
                data[key] = value
        if isinstance(raw.get("calendars"), dict):
            data["calendars"] = raw["calendars"]
        if isinstance(raw.get("meta"), dict):
            data["meta"] = raw["meta"]
        data["places"] = normalize_places(raw.get("places"))
        return data

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return self.default_data()
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception:
                return self.default_data()
            return self._normalize_data(raw)

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            normalized = self._normalize_data(data)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(self.path.parent), delete=False) as tf:
                json.dump(normalized, tf, ensure_ascii=False, indent=2)
                name = tf.name
            os.replace(name, self.path)


storage = JsonStorage(DATA_FILE)
