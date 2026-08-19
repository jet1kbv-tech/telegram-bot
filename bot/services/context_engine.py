"""Read-only, deterministic cross-domain context projection.

The module accepts a normalized storage snapshot and never performs I/O or
mutation. Telegram/provider identifiers are intentionally absent from results.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from bot.services.event_attachment_query import attachment_visible_parent
from bot.storage import event_effective_end_dt, parse_event_dt, parse_calendar_event_end_dt, parse_calendar_event_start_dt

TRIP_LINK_WINDOW = timedelta(days=3)
RETURN_LINK_WINDOW = timedelta(days=14)
_SEPARATORS = re.compile(r"[^\w]+", re.UNICODE)
_VORONEZH = re.compile(r"(?:^|\s)воронеж(?:\s|$)")


@dataclass(frozen=True, slots=True)
class EventContext:
    context_id: str
    source_type: str
    canonical_parent_type: str
    canonical_parent_id: str
    title: str
    date: date
    start_time: time | None
    end_date: date | None
    end_time: time | None
    owner_scope: str
    is_shared: bool
    location_text: str | None


@dataclass(frozen=True, slots=True)
class DocumentContext:
    attachment_id: str
    parent_type: str
    parent_id: str
    semantic_type: str
    transport_type: str | None
    origin: str | None
    destination: str | None
    departure_date: date | None
    departure_time: time | None
    arrival_date: date | None
    arrival_time: time | None
    person: str | None
    media_type: str


@dataclass(frozen=True, slots=True)
class TripContext:
    context_id: str
    destination: str
    destination_key: str
    city_hint: str | None
    origin: str | None
    trip_start: datetime
    trip_end: datetime
    departure_date: date | None
    departure_time: time | None
    arrival_date: date | None
    arrival_time: time | None
    linked_event_ids: tuple[str, ...]
    linked_attachment_ids: tuple[str, ...]
    directions: tuple[tuple[str, str], ...]
    confidence: str
    match_reasons: tuple[str, ...]
    actor_scope: str


@dataclass(frozen=True, slots=True)
class ContextDiagnostics:
    event_count: int
    document_count: int
    trip_count: int
    link_count: int
    date_range_applied: bool


@dataclass(frozen=True, slots=True)
class ContextBundle:
    events: tuple[EventContext, ...]
    documents: tuple[DocumentContext, ...]
    trips: tuple[TripContext, ...]
    diagnostics: ContextDiagnostics


def normalize_location_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
    return " ".join(_SEPARATORS.sub(" ", normalized).split())


def extract_city_hint(value: str | None) -> str | None:
    """Return only explicitly allow-listed, unambiguous city tokens.

    The small v1 allow-list deliberately recognizes Voronezh station variants;
    it does not equate Moscow with Moscow Oblast or pretend to be a geocoder.
    """
    normalized = normalize_location_text(value)
    if _VORONEZH.search(normalized):
        return "Воронеж"
    return None


def _location_key(value: str | None) -> str:
    return (extract_city_hint(value) or normalize_location_text(value)).casefold()


def _opaque(prefix: str, identities: Iterable[str]) -> str:
    digest = hashlib.sha256("\x1f".join(sorted(identities)).encode()).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _timezone(value: str | tzinfo) -> tzinfo:
    return ZoneInfo(value) if isinstance(value, str) else value


def _local_naive(now: datetime, zone: tzinfo) -> datetime:
    return now.astimezone(zone).replace(tzinfo=None) if now.tzinfo else now


def _as_date(value: date | str | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _clock(value: Any) -> time | None:
    try:
        return time.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def collect_visible_events(data: dict[str, Any], actor_key: str, now: datetime, timezone: str | tzinfo,
                           date_from: date | str | None = None, date_to: date | str | None = None,
                           include_past: bool = False) -> tuple[EventContext, ...]:
    zone, lower, upper = _timezone(timezone), _as_date(date_from), _as_date(date_to)
    local_now = _local_naive(now, zone)
    rows: list[tuple[datetime, EventContext]] = []
    for item in data.get("calendars", {}).get(actor_key, []):
        if not isinstance(item, dict) or item.get("source") != "manual":
            continue
        start, end = parse_calendar_event_start_dt(item), parse_calendar_event_end_dt(item)
        if start:
            context = EventContext(_opaque("evt", ["calendar", str(item.get("id"))]), "calendar", "calendar",
                str(item.get("id") or ""), str(item.get("title") or ""), start.date(), start.time(),
                end.date() if end else None, end.time() if end else None, actor_key, False, None)
            rows.append((start, context))
    for item in data.get("afisha", []):
        if not isinstance(item, dict) or item.get("status") != "active":
            continue
        start, end = parse_event_dt(item), event_effective_end_dt(item)
        if start:
            context = EventContext(_opaque("evt", ["afisha", str(item.get("id"))]), "afisha", "afisha",
                str(item.get("id") or ""), str(item.get("title") or ""), start.date(), start.time(),
                end.date() if end else None, end.time() if end else None, "shared", True,
                str(item.get("place") or "").strip() or None)
            rows.append((start, context))
    selected = [(stamp, context) for stamp, context in rows
                if (include_past or datetime.combine(context.end_date or context.date, context.end_time or context.start_time or time.min) >= local_now)
                and (not lower or context.date >= lower) and (not upper or context.date <= upper)]
    return tuple(context for _, context in sorted(selected, key=lambda row: (row[0], row[1].canonical_parent_type, row[1].canonical_parent_id)))


def collect_visible_documents(data: dict[str, Any], actor_key: str,
                              visible_events: Iterable[EventContext]) -> tuple[DocumentContext, ...]:
    parents = {(event.canonical_parent_type, event.canonical_parent_id) for event in visible_events}
    result = []
    for item in data.get("event_attachments", []):
        if not isinstance(item, dict):
            continue
        parent = attachment_visible_parent(data, item, actor_key)
        if not parent or parent not in parents:
            continue
        result.append(DocumentContext(str(item.get("id") or ""), parent[0], parent[1],
            str(item.get("semantic_type") or "other"), item.get("transport_type"), item.get("origin"),
            item.get("destination"), _as_date(item.get("date")), _clock(item.get("departure_time")),
            _as_date(item.get("arrival_date")), _clock(item.get("arrival_time")), item.get("person"),
            str(item.get("telegram_media_type") or "")))
    return tuple(sorted(result, key=lambda document: document.attachment_id))


def _stamp(day: date | None, clock: time | None) -> datetime | None:
    return datetime.combine(day, clock or time.min) if day else None


def _document_bounds(document: DocumentContext) -> tuple[datetime | None, datetime | None]:
    departure = _stamp(document.departure_date, document.departure_time)
    arrival = _stamp(document.arrival_date, document.arrival_time)
    return departure or arrival, arrival or departure


def infer_trip_contexts(events: Iterable[EventContext], documents: Iterable[DocumentContext], actor_key: str) -> tuple[TripContext, ...]:
    event_by_parent = {(event.canonical_parent_type, event.canonical_parent_id): event for event in events}
    candidates = [document for document in documents if document.semantic_type == "transport_ticket"
                  and document.destination and (document.departure_date or document.arrival_date)]
    candidates.sort(key=lambda document: (_document_bounds(document)[0] or datetime.max, document.attachment_id))
    groups: list[list[DocumentContext]] = []
    for document in candidates:
        start, _ = _document_bounds(document)
        destination_key, origin_key = _location_key(document.destination), _location_key(document.origin)
        matched = None
        for group in reversed(groups):
            first, last = _document_bounds(group[0])[0], _document_bounds(group[-1])[1]
            primary_destination, primary_origin = _location_key(group[0].destination), _location_key(group[0].origin)
            same_destination = destination_key == primary_destination and start and last and start - last <= TRIP_LINK_WINDOW
            opposite_route = origin_key == primary_destination and destination_key == primary_origin and start and first and timedelta(0) <= start - first <= RETURN_LINK_WINDOW
            if same_destination or opposite_route:
                matched = group
                break
        (matched if matched is not None else groups.append([]) or groups[-1]).append(document)

    trips = []
    for group in groups:
        starts, ends = zip(*(_document_bounds(document) for document in group))
        first = group[0]
        event_ids = sorted({event_by_parent[(document.parent_type, document.parent_id)].context_id
                            for document in group if (document.parent_type, document.parent_id) in event_by_parent})
        reasons = {"transport_ticket", "structured_destination", "structured_date"}
        if event_ids:
            reasons.update(("same_parent", "attachment_parent_event"))
        if len(group) > 1:
            reasons.update(("compatible_date_window",))
            if any(_location_key(document.origin) == _location_key(first.destination)
                   and _location_key(document.destination) == _location_key(first.origin) for document in group[1:]):
                reasons.add("opposite_transport_route")
            else:
                reasons.add("same_destination")
        directions = []
        for document in group:
            direction = "outbound"
            if document is not first and _location_key(document.origin) == _location_key(first.destination) and _location_key(document.destination) == _location_key(first.origin):
                direction = "return"
            elif document is not first:
                direction = "unknown"
            directions.append((document.attachment_id, direction))
        trip_start, trip_end = min(value for value in starts if value), max(value for value in ends if value)
        trips.append(TripContext(_opaque("trip", [document.attachment_id for document in group]),
            str(first.destination), _location_key(first.destination), extract_city_hint(first.destination), first.origin,
            trip_start, trip_end, first.departure_date, first.departure_time, first.arrival_date, first.arrival_time,
            tuple(event_ids), tuple(sorted(document.attachment_id for document in group)), tuple(directions),
            "strong" if event_ids or len(group) > 1 else "medium", tuple(sorted(reasons)), actor_key))
    return tuple(sorted(trips, key=lambda trip: (trip.trip_start, trip.context_id)))


def build_context_bundle(data: dict[str, Any], actor_key: str, now: datetime, timezone: str | tzinfo,
                         date_from: date | str | None = None, date_to: date | str | None = None,
                         include_past: bool = False) -> ContextBundle:
    events = collect_visible_events(data, actor_key, now, timezone, date_from, date_to, include_past)
    documents = collect_visible_documents(data, actor_key, events)
    trips = infer_trip_contexts(events, documents, actor_key)
    return ContextBundle(events, documents, trips, ContextDiagnostics(len(events), len(documents), len(trips),
        sum(len(trip.linked_event_ids) + len(trip.linked_attachment_ids) for trip in trips), bool(date_from or date_to)))


def find_trip_contexts(bundle: ContextBundle) -> tuple[TripContext, ...]:
    return bundle.trips


def find_trip_by_destination(bundle: ContextBundle, destination: str) -> tuple[TripContext, ...]:
    key = _location_key(destination)
    return tuple(trip for trip in bundle.trips if trip.destination_key == key)


def find_event_context(bundle: ContextBundle, parent_type: str, parent_id: str) -> EventContext | None:
    return next((event for event in bundle.events if (event.canonical_parent_type, event.canonical_parent_id) == (parent_type, parent_id)), None)


def documents_for_context(bundle: ContextBundle, context: TripContext | EventContext) -> tuple[DocumentContext, ...]:
    if isinstance(context, TripContext):
        identities = set(context.linked_attachment_ids)
        return tuple(document for document in bundle.documents if document.attachment_id in identities)
    return tuple(document for document in bundle.documents
                 if (document.parent_type, document.parent_id) == (context.canonical_parent_type, context.canonical_parent_id))
