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
from bot.services.context_sessions import clear_context_session, get_context_session, set_context_session

_MONTHS = ("", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
           "августа", "сентября", "октября", "ноября", "декабря")
_WEEKDAYS = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")

FOLLOW_UP_COMPATIBILITY = {
    "event": {
        "event_place": "event_place", "event_date": "event_date", "event_time": "event_time",
        "event_documents": "event_documents", "documents": "event_documents",
    },
    "trip": {
        "departure": "departure", "event_time": "departure", "arrival": "arrival", "return": "return",
        "documents": "documents", "event_documents": "documents", "origin": "origin", "destination": "destination",
    },
}


@dataclass(frozen=True, slots=True)
class ContextQueryResult:
    outcome: str
    text: str
    candidate_count: int
    trip: TripContext | None = None
    event: EventContext | None = None

    @property
    def subject(self) -> tuple[str, str] | None:
        if self.event is not None:
            return "event", self.event.context_id
        if self.trip is not None:
            return "trip", self.trip.context_id
        return None


def _follow_up_clarification(query_type: str) -> ContextQueryResult:
    if query_type in {"return", "arrival", "departure", "origin", "destination"}:
        text = "Не понял, к какой поездке относится вопрос. Уточни поездку."
    elif query_type in {"event_place", "event_date"}:
        text = "Не понял, о каком событии речь. Уточни, пожалуйста."
    else:
        text = "Не понял, о каком событии или поездке речь. Уточни, пожалуйста."
    return ContextQueryResult("missing_context", text, 0)


def execute_context_query(data: dict[str, Any], *, actor_key: str, now: datetime, timezone: str,
                          follow_up: bool = False, **arguments: Any) -> ContextQueryResult:
    """Apply bounded pointer lifecycle around the read-only Context Engine query."""
    if not follow_up:
        answer = query_context(data, actor_key=actor_key, now=now, timezone=timezone, **arguments)
        if answer.subject is not None and answer.candidate_count == 1:
            set_context_session(data, actor_key, *answer.subject, now)
        return answer
    session = get_context_session(data, actor_key, now)
    if session is None:
        return _follow_up_clarification(arguments["query_type"])
    compatible = FOLLOW_UP_COMPATIBILITY.get(session.domain, {}).get(arguments["query_type"])
    if compatible is None:
        return _follow_up_clarification(arguments["query_type"])
    answer = query_context(data, actor_key=actor_key, now=now, timezone=timezone,
                           query_type=compatible, context_id=session.context_id,
                           context_domain=session.domain)
    if answer.candidate_count == 0:
        clear_context_session(data, actor_key)
        return ContextQueryResult(
            "unavailable", "Это событие или поездка больше недоступны. Уточни, о чём хочешь спросить.", 0)
    if compatible == "event_place" and answer.event is not None and answer.event.location_text:
        return ContextQueryResult(answer.outcome, f"{answer.event.location_text}.", answer.candidate_count,
                                  event=answer.event)
    return answer


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
                 date_expression: str | None, now: datetime, timezone: str,
                 context_id: str | None = None) -> ContextQueryResult:
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

    if context_id:
        candidates = tuple(event for event in bundle.events if event.context_id == context_id)
        outcome, event = ("found", candidates[0]) if len(candidates) == 1 else ("not_found", None)
    elif query_type == "next_event":
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
        return ContextQueryResult("missing", f"{title} нашёл, но время для него не указано.", 1, event=event)
    if query_type in {"next_event", "event_date", "event_time"}:
        value = date_time_text(event.date, event.start_time if query_type != "event_date" else None)
        if not value:
            return ContextQueryResult("missing", f"{title} нашёл, но время для него не указано.", 1, event=event)
        return ContextQueryResult("found", f"📅 {title} — {value}.", 1, event=event)
    if query_type == "event_place":
        text = f"{title} — {event.location_text}." if event.location_text else f"{title} нашёл, но место для него не указано."
        return ContextQueryResult("found" if event.location_text else "missing", text, 1, event=event)
    documents = documents_for_context(bundle, event)
    if not documents:
        return ContextQueryResult("missing", f"К событию «{title}» документов не прикреплено.", 1, event=event)
    return ContextQueryResult("found", "\n".join([f"📎 К событию «{title}» прикреплено:", *[f"• {_document_label(row)}" for row in documents]]), 1, event=event)


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
                  date_expression: str | None = None, person: str | None = None,
                  context_id: str | None = None, context_domain: str | None = None) -> ContextQueryResult:
    """Resolve facts locally; provider-derived arguments never contain actor or facts."""
    # ``person`` is intentionally ignored for authorization: actor scope is
    # derived by the application and cannot be widened by provider output.
    event_query = query_type in {"events", "next_event", "event_time", "event_date", "event_place", "event_documents"}
    bundle = build_context_bundle(data, actor_key, now, timezone, include_past=not event_query)
    if event_query:
        return _event_query(bundle, query_type, target, date_expression, now, timezone,
                            context_id if context_domain == "event" else None)
    trips = (tuple(trip for trip in bundle.trips if trip.context_id == context_id)
             if context_domain == "trip" and context_id else
             find_trip_by_destination(bundle, destination) if destination else find_trip_contexts(bundle))
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
    if query_type == "origin":
        return ContextQueryResult("found", trip.origin, 1, trip) if trip.origin else ContextQueryResult(
            "missing", "Поездку нашёл, но место отправления не указано.", 1, trip)
    if query_type == "destination":
        return ContextQueryResult("found", trip.destination, 1, trip) if trip.destination else ContextQueryResult(
            "missing", "Поездку нашёл, но пункт назначения не указан.", 1, trip)
    lines = [f"🧳 Поездка в {trip.city_hint or trip.destination}"]
    if outbound:
        lines += [""] + _block("Туда", outbound)
        arrival = date_time_text(outbound.arrival_date, outbound.arrival_time)
        if arrival: lines += ["", "Прибытие:", arrival]
    if returning:
        lines += [""] + _block("Обратно", returning)
    lines += ["", f"Документы: {len(documents)}"]
    return ContextQueryResult("found", "\n".join(lines), 1, trip)
