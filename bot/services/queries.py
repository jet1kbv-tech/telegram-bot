from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

from bot.handlers.film_filters import film_genres, genre_identity
from bot.services.nl_entity_resolution import normalize_reference
from bot.storage import normalize_purchase_price, parse_calendar_event_start_dt, parse_event_dt


@dataclass(frozen=True, slots=True)
class QueryResult:
    items: list[dict[str, Any]]
    total: int
    amount: int = 0
    missing_prices: int = 0


def query_purchases(data: dict[str, Any], *, status: str, priority: str, buyer: str,
                    actor_name: str, other_name: str = "") -> QueryResult:
    rows: list[tuple[int, dict[str, Any]]] = []
    buckets = ("planned", "bought") if status == "any" else (status,)
    index = 0
    for bucket in buckets:
        for raw in data.get("purchases", {}).get(bucket, []):
            item = dict(raw, _status=bucket)
            assigned = str(item.get("buyer") or "")
            buyer_ok = buyer == "any" or (buyer == "unassigned" and not assigned) or (buyer == "current_user" and assigned == actor_name) or (buyer == "other_user" and bool(assigned) and assigned != actor_name and (not other_name or assigned == other_name))
            if (priority == "any" or item.get("priority") == priority) and buyer_ok:
                rows.append((index, item))
            index += 1
    rank = {"high": 0, "medium": 1, "low": 2, "": 3, None: 3}
    items = [item for _, item in sorted(rows, key=lambda pair: (rank.get(pair[1].get("priority"), 3), pair[0]))]
    prices = [normalize_purchase_price(item.get("price")) for item in items]
    return QueryResult(items, len(items), sum(value for value in prices if value is not None), sum(value is None for value in prices))


def query_films(data: dict[str, Any], *, status: str, media_type: str, genre: str | None,
                chooser: Callable[[list[dict[str, Any]]], dict[str, Any]] = random.choice) -> QueryResult:
    identity = genre_identity(genre) if genre else ""
    items = []
    for item in data.get("films", []):
        if status != "any" and item.get("status") != status:
            continue
        if media_type != "any" and item.get("media_type") != media_type:
            continue
        if identity and identity not in film_genres(item):
            continue
        items.append(item)
    return QueryResult(items, len(items))


def choose_random(result: QueryResult, chooser: Callable[[list[dict[str, Any]]], dict[str, Any]] = random.choice) -> dict[str, Any] | None:
    return chooser(result.items) if result.items else None


def query_calendar(data: dict[str, Any], *, owner: str, date_from: str | None, date_to: str | None,
                   target: str | None, now: datetime) -> QueryResult:
    # The owner's stored calendar deliberately includes manual records and read-only Afisha projections.
    return _query_events(data.get("calendars", {}).get(owner, []), date_from, date_to, target, now, parse_calendar_event_start_dt)


def query_afisha(data: dict[str, Any], *, date_from: str | None, date_to: str | None,
                 target: str | None, now: datetime) -> QueryResult:
    # Source records only; calendar projections are never queried here.
    return _query_events(data.get("afisha", []), date_from, date_to, target, now, parse_event_dt)


def _query_events(items: list[dict[str, Any]], date_from: str | None, date_to: str | None, target: str | None,
                  now: datetime, parser: Callable[[dict[str, Any]], datetime | None]) -> QueryResult:
    lower = date.fromisoformat(date_from) if date_from else None
    upper = date.fromisoformat(date_to) if date_to else None
    needle = normalize_reference(target or "")
    selected = []
    for item in items:
        stamp = parser(item)
        if stamp is None or (lower and stamp.date() < lower) or (upper and stamp.date() > upper):
            continue
        if needle and needle not in normalize_reference(str(item.get("title") or "")):
            continue
        selected.append(item)
    selected.sort(key=lambda item: parser(item) or datetime.max)
    return QueryResult(selected, len(selected))


def next_event(result: QueryResult, parser: Callable[[dict[str, Any]], datetime | None], now: datetime) -> dict[str, Any] | None:
    local_now = now.replace(tzinfo=None)
    return next((item for item in result.items if parser(item) and parser(item) >= local_now), None)
