"""Deterministic, read-only queries over Context Engine projections."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bot.services.context_engine import (
    ContextBundle, DocumentContext, TripContext, build_context_bundle,
    documents_for_context, find_trip_by_destination, find_trip_contexts,
)
from bot.services.event_attachment_display import date_time_text, transport_icon_label


@dataclass(frozen=True, slots=True)
class ContextQueryResult:
    outcome: str
    text: str
    candidate_count: int
    trip: TripContext | None = None


def _ticket(bundle: ContextBundle, trip: TripContext, direction: str) -> DocumentContext | None:
    wanted = {identity for identity, value in trip.directions if value == direction}
    rows = [row for row in documents_for_context(bundle, trip) if row.attachment_id in wanted]
    return rows[0] if len(rows) == 1 else None


def _route(document: DocumentContext) -> str | None:
    return f"{document.origin} → {document.destination}" if document.origin and document.destination else document.origin or document.destination


def _heading(trip: TripContext, transport_type: str | None = None) -> str:
    icon, _ = transport_icon_label(transport_type)
    return f"{icon} {trip.city_hint or trip.destination}"


def _block(label: str, document: DocumentContext, *, arrival: bool = False) -> list[str]:
    day = document.arrival_date if arrival else document.departure_date
    clock = document.arrival_time if arrival else document.departure_time
    value = date_time_text(day, clock)
    lines = [f"{label}:", value] if value else [f"{label}: данные не сохранены"]
    route = _route(document)
    if route:
        lines.append(route)
    return lines


def query_context(data: dict[str, Any], *, actor_key: str, now: datetime, timezone: str,
                  query_type: str, destination: str | None = None,
                  transport_type: str | None = None) -> ContextQueryResult:
    """Resolve facts locally; provider-derived arguments never contain actor or facts."""
    bundle = build_context_bundle(data, actor_key, now, timezone, include_past=True)
    trips = find_trip_by_destination(bundle, destination) if destination else find_trip_contexts(bundle)
    if transport_type:
        trips = tuple(trip for trip in trips if any(
            row.transport_type == transport_type for row in documents_for_context(bundle, trip)
            if row.semantic_type == "transport_ticket"))
    if not trips:
        suffix = f" в {destination}" if destination else ""
        return ContextQueryResult("not_found", f"Не нашёл сохранённую поездку{suffix}.", 0)
    if len(trips) != 1:
        return ContextQueryResult("ambiguous", "Нашёл несколько подходящих поездок. Не могу однозначно определить нужную.", len(trips))
    trip = trips[0]
    outbound = _ticket(bundle, trip, "outbound")
    returning = _ticket(bundle, trip, "return")
    documents = documents_for_context(bundle, trip)
    if query_type == "departure":
        if not outbound:
            return ContextQueryResult("missing", "Поездку нашёл, но отправление не удалось определить однозначно.", 1, trip)
        value = date_time_text(outbound.departure_date, outbound.departure_time)
        if not value:
            return ContextQueryResult("missing", "Поездку нашёл, но дата и время отправления пока не сохранены.", 1, trip)
        lines = [_heading(trip, outbound.transport_type), "", "Отправление:", value]
        if outbound.origin: lines.append(outbound.origin)
        return ContextQueryResult("found", "\n".join(lines), 1, trip)
    if query_type == "arrival":
        if not outbound:
            return ContextQueryResult("missing", "Поездку нашёл, но прибытие не удалось определить однозначно.", 1, trip)
        value = date_time_text(outbound.arrival_date, outbound.arrival_time)
        if not value:
            return ContextQueryResult("missing", "Поездку нашёл, но информация о прибытии в билете пока не сохранена.", 1, trip)
        lines = [_heading(trip, outbound.transport_type), "", "Прибытие:", value]
        if outbound.destination: lines.append(outbound.destination)
        return ContextQueryResult("found", "\n".join(lines), 1, trip)
    if query_type == "return":
        if not returning:
            return ContextQueryResult("missing", "Поездку нашёл, но обратный путь определить не удалось.", 1, trip)
        value = date_time_text(returning.departure_date, returning.departure_time)
        if not value:
            return ContextQueryResult("missing", "Обратный билет нашёл, но дата и время отправления пока не сохранены.", 1, trip)
        return ContextQueryResult("found", "\n".join([_heading(trip, returning.transport_type), "", "Обратно:", value] + ([_route(returning)] if _route(returning) else [])), 1, trip)
    if query_type == "documents":
        count = len(documents)
        return ContextQueryResult("found", f"🧳 Поездка в {trip.city_hint or trip.destination}\n\nДокументы: {count}", 1, trip)
    lines = [f"🧳 Поездка в {trip.city_hint or trip.destination}"]
    if outbound:
        lines += [""] + _block("Туда", outbound)
        arrival = date_time_text(outbound.arrival_date, outbound.arrival_time)
        if arrival: lines += ["", "Прибытие:", arrival]
    if returning:
        lines += [""] + _block("Обратно", returning)
    lines += ["", f"Документы: {len(documents)}"]
    return ContextQueryResult("found", "\n".join(lines), 1, trip)
