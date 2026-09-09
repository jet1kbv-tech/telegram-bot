"""Deterministic, read-only queries over Context Engine projections."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from bot.services.context_engine import (
    ContextBundle, DocumentContext, EventContext, TripContext, build_context_bundle,
    documents_for_context, find_trip_by_destination, find_trip_contexts,
)
from bot.services.event_attachment_display import date_time_text, transport_icon_label
from bot.services.nl_dates import resolve_date_range, zoned_now
from bot.services.nl_entity_resolution import normalize_reference

_MONTHS = ("", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
           "августа", "сентября", "октября", "ноября", "декабря")
_WEEKDAYS = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")


@dataclass(frozen=True, slots=True)
class ContextQueryResult:
    outcome: str
    text: str
    candidate_count: int
    trip: TripContext | None = None


def _event_candidates(bundle: ContextBundle, target: str | None) -> tuple[EventContext, ...]:
    needle = normalize_reference(target or "")
    return tuple(event for event in bundle.events
                 if not needle or needle in normalize_reference(event.title))


def _event_label(event: EventContext) -> str:
    return f"{event.title or 'Событие'} — {event.date.day} {_MONTHS[event.date.month]}"


def _select_event(bundle: ContextBundle, target: str | None) -> tuple[str, EventContext | None, tuple[EventContext, ...]]:
    candidates = _event_candidates(bundle, target)
    if not candidates:
        return "not_found", None, candidates
    if len(candidates) > 1:
        return "ambiguous", None, candidates
    return "found", candidates[0], candidates


def _ambiguity(candidates: tuple[EventContext, ...]) -> str:
    lines = ["Нашёл несколько событий:", ""]
    lines.extend(f"{index}. {_event_label(event)}" for index, event in enumerate(candidates, 1))
    return "\n".join(lines + ["", "Какое ты имеешь в виду?"])


def _document_label(document: DocumentContext) -> str:
    if document.semantic_type == "transport_ticket":
        return transport_icon_label(document.transport_type)[1].casefold()
    return {"voucher": "ваучер / проживание", "reservation": "бронь", "insurance": "страховка"}.get(
        document.semantic_type, "документ")


def _event_query(bundle: ContextBundle, query_type: str, target: str | None,
                 date_expression: str | None, now: datetime, timezone: str) -> ContextQueryResult:
    if query_type == "events":
        if not date_expression:
            return ContextQueryResult("missing", "Уточни дату или период.", 0)
        lower, upper = resolve_date_range(date_expression, now=now, timezone=timezone)
        ranged = tuple(event for event in bundle.events if date.fromisoformat(lower) <= event.date <= date.fromisoformat(upper))
        if not ranged:
            return ContextQueryResult("not_found", f"На {date_expression.strip()} ничего не запланировано.", 0)
        first = date.fromisoformat(lower)
        heading = f"📅 {_WEEKDAYS[first.weekday()]}, {first.day} {_MONTHS[first.month]}" if lower == upper else f"📅 {date_expression.strip().capitalize()}"
        lines = []
        for event in ranged:
            prefix = event.start_time.strftime("%H:%M") if event.start_time else event.date.strftime("%d.%m")
            place = f" · {event.location_text}" if event.location_text else ""
            lines.append(f"{prefix} — {event.title or 'Событие'}{place}")
        return ContextQueryResult("found", "\n".join([heading, "", *lines]), len(ranged))

    if query_type == "next_event":
        local_now = zoned_now(timezone, now).replace(tzinfo=None)
        future = tuple(event for event in _event_candidates(bundle, target)
                       if datetime.combine(event.date, event.start_time or datetime.min.time()) >= local_now)
        if not future:
            return ContextQueryResult("not_found", "Не нашёл такое событие.", 0)
        outcome, event, candidates = "found", future[0], future
    else:
        outcome, event, candidates = _select_event(bundle, target)
    if outcome == "not_found":
        return ContextQueryResult(outcome, "Не нашёл такое событие.", 0)
    if outcome == "ambiguous":
        return ContextQueryResult(outcome, _ambiguity(candidates), len(candidates))
    assert event is not None
    title = event.title or "Событие"
    if query_type == "event_time" and event.start_time is None:
        return ContextQueryResult("missing", f"{title} нашёл, но время для него не указано.", 1)
    if query_type in {"next_event", "event_date", "event_time"}:
        value = date_time_text(event.date, event.start_time if query_type != "event_date" else None)
        if not value:
            return ContextQueryResult("missing", f"{title} нашёл, но время для него не указано.", 1)
        return ContextQueryResult("found", f"📅 {title} — {value}.", 1)
    if query_type == "event_place":
        text = f"{title} — {event.location_text}." if event.location_text else f"{title} нашёл, но место для него не указано."
        return ContextQueryResult("found" if event.location_text else "missing", text, 1)
    documents = documents_for_context(bundle, event)
    if not documents:
        return ContextQueryResult("missing", f"К событию «{title}» документов не прикреплено.", 1)
    return ContextQueryResult("found", "\n".join([f"📎 К событию «{title}» прикреплено:", *[f"• {_document_label(row)}" for row in documents]]), 1)


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
                  transport_type: str | None = None, target: str | None = None,
                  date_expression: str | None = None, person: str | None = None) -> ContextQueryResult:
    """Resolve facts locally; provider-derived arguments never contain actor or facts."""
    # ``person`` is intentionally ignored for authorization: actor scope is
    # derived by the application and cannot be widened by provider output.
    event_query = query_type in {"events", "next_event", "event_time", "event_date", "event_place", "event_documents"}
    bundle = build_context_bundle(data, actor_key, now, timezone, include_past=not event_query)
    if event_query:
        return _event_query(bundle, query_type, target, date_expression, now, timezone)
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
        route = _route(outbound)
        lines = [f"{transport_icon_label(outbound.transport_type)[0]} {route or trip.city_hint or trip.destination}", value]
        return ContextQueryResult("found", "\n".join(lines), 1, trip)
    if query_type == "arrival":
        if not outbound:
            return ContextQueryResult("missing", "Поездку нашёл, но прибытие не удалось определить однозначно.", 1, trip)
        value = date_time_text(outbound.arrival_date, outbound.arrival_time)
        if not value:
            return ContextQueryResult("missing", "Поездку нашёл, но информация о прибытии в билете пока не сохранена.", 1, trip)
        route = _route(outbound)
        lines = [f"{transport_icon_label(outbound.transport_type)[0]} {route or trip.city_hint or trip.destination}", f"Прибытие: {value}"]
        return ContextQueryResult("found", "\n".join(lines), 1, trip)
    if query_type == "return":
        if not returning:
            return ContextQueryResult("missing", "Поездку нашёл, но обратный путь определить не удалось.", 1, trip)
        value = date_time_text(returning.departure_date, returning.departure_time)
        if not value:
            return ContextQueryResult("missing", "Обратный билет нашёл, но дата и время отправления пока не сохранены.", 1, trip)
        return ContextQueryResult("found", "\n".join([_heading(trip, returning.transport_type), "", "Обратно:", value] + ([_route(returning)] if _route(returning) else [])), 1, trip)
    if query_type == "documents":
        if not documents:
            return ContextQueryResult("missing", "К этой поездке документов не прикреплено.", 1, trip)
        return ContextQueryResult("found", "\n".join([
            f"📎 К поездке в {trip.city_hint or trip.destination} прикреплено:",
            *[f"• {_document_label(row)}" for row in documents],
        ]), 1, trip)
    lines = [f"🧳 Поездка в {trip.city_hint or trip.destination}"]
    if outbound:
        lines += [""] + _block("Туда", outbound)
        arrival = date_time_text(outbound.arrival_date, outbound.arrival_time)
        if arrival: lines += ["", "Прибытие:", arrival]
    if returning:
        lines += [""] + _block("Обратно", returning)
    lines += ["", f"Документы: {len(documents)}"]
    return ContextQueryResult("found", "\n".join(lines), 1, trip)
