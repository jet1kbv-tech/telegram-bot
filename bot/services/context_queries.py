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
from bot.services.cross_context_queries import (
    date_bounds, direction_document, events_on_arrival, events_overlapping_trip,
    in_range, limited, trip_documents,
)
from bot.services.trip_briefing import TripBriefing, build_trip_briefing, render_trip_briefing
from bot.services.upcoming_brief import UpcomingBrief, build_upcoming_brief, render_upcoming_brief, resolve_brief_range

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
        "trip_briefing": "trip_briefing",
    },
}


@dataclass(frozen=True, slots=True)
class ContextQueryResult:
    outcome: str
    text: str
    candidate_count: int
    trip: TripContext | None = None
    event: EventContext | None = None
    briefing: TripBriefing | None = None
    upcoming_brief: UpcomingBrief | None = None

    @property
    def subject(self) -> tuple[str, str] | None:
        if self.event is not None:
            return "event", self.event.context_id
        if self.trip is not None:
            return "trip", self.trip.context_id
        return None


def _follow_up_clarification(query_type: str) -> ContextQueryResult:
    if query_type in {"return", "arrival", "departure", "origin", "destination", "trip_briefing"}:
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


def _event_row(event: EventContext) -> str:
    clock = f", {event.start_time:%H:%M}" if event.start_time else ""
    return f"• {event.date.day} {_MONTHS[event.date.month]}{clock} — {event.title or 'Событие'}"


def _trip_row(trip: TripContext) -> str:
    return f"• {trip.city_hint or trip.destination} — выезд {trip.trip_start.day} {_MONTHS[trip.trip_start.month]}"


def _with_remainder(lines: list[str], remainder: int) -> list[str]:
    return lines + ([f"И ещё {remainder}…"] if remainder else [])


def _person_allows_event(event: EventContext, person: str | None, actor_key: str) -> bool:
    # A provider name can narrow the already actor-scoped bundle, never widen it.
    if person in {None, "self"}:
        return True
    if person == "both" or person != actor_key:
        return event.is_shared
    return event.is_shared or event.owner_scope == actor_key


def _person_allows_trip(bundle: ContextBundle, trip: TripContext, person: str | None,
                        actor_key: str) -> bool:
    if person in {None, "self", actor_key}:
        return True
    linked = set(trip.linked_event_ids)
    return bool(linked) and all(event.is_shared for event in bundle.events if event.context_id in linked)


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


def _cross_query(bundle: ContextBundle, *, query_type: str, destination: str | None,
                 transport_type: str | None, date_expression: str | None,
                 person: str | None, semantic_type: str | None, actor_key: str,
                 now: datetime, timezone: str) -> ContextQueryResult:
    bounds = date_bounds(date_expression, now, timezone)
    trips = find_trip_by_destination(bundle, destination) if destination else find_trip_contexts(bundle)
    trips = tuple(trip for trip in trips
                  if in_range(trip.trip_start.date(), bounds)
                  and _person_allows_trip(bundle, trip, person, actor_key))
    if transport_type:
        trips = tuple(trip for trip in trips if any(
            document.transport_type == transport_type
            for document in trip_documents(bundle, trip)
            if document.semantic_type == "transport_ticket"))

    if query_type in {"events_during_trip", "events_on_trip_arrival"}:
        if not trips:
            suffix = f" в {destination}" if destination else ""
            return ContextQueryResult("not_found", f"Не нашёл сохранённую поездку{suffix}.", 0)
        if len(trips) > 1:
            rows, remainder = limited(trips)
            text = _with_remainder(["Нашёл несколько подходящих поездок:", "", *map(_trip_row, rows)], remainder)
            return ContextQueryResult("ambiguous", "\n".join([*text, "", "Какую ты имеешь в виду?"]), len(trips))
        trip = trips[0]
        events = (events_overlapping_trip(bundle, trip) if query_type == "events_during_trip"
                  else events_on_arrival(bundle, trip))
        if events is None:
            detail = ("дату окончания" if query_type == "events_during_trip" else "дату прибытия")
            return ContextQueryResult("missing", f"Поездку нашёл, но не могу надёжно определить {detail}.", 1, trip)
        events = tuple(event for event in events if _person_allows_event(event, person, actor_key))
        place = trip.city_hint or trip.destination
        if not events:
            text = ("На время этой поездки других событий не найдено." if query_type == "events_during_trip"
                    else "В день приезда других событий не найдено.")
            return ContextQueryResult("not_found", text, 1, trip)
        rows, remainder = limited(events)
        heading = (f"На время поездки в {place}:" if query_type == "events_during_trip"
                   else f"В день приезда в {place}:")
        return ContextQueryResult("found", "\n".join(_with_remainder([heading, "", *map(_event_row, rows)], remainder)), 1, trip)

    if query_type in {"trips_missing_documents", "trips_missing_return"}:
        if query_type == "trips_missing_return":
            rows = tuple(trip for trip in trips
                         if direction_document(bundle, trip, "outbound") is not None
                         and direction_document(bundle, trip, "return") is None)
            heading = "Без прикреплённого обратного транспортного сегмента:"
        else:
            rows = tuple(trip for trip in trips if not any(
                semantic_type is None or document.semantic_type == semantic_type
                for document in trip_documents(bundle, trip)))
            heading = "Без документов:" if semantic_type is None else f"Без документов типа «{_semantic_label(semantic_type)}»:"
        if not rows:
            return ContextQueryResult("not_found", "Подходящих поездок не найдено.", 0)
        rows = tuple(sorted(rows, key=lambda trip: (trip.trip_start, trip.context_id)))
        visible, remainder = limited(rows)
        return ContextQueryResult("found", "\n".join(_with_remainder([heading, "", *map(_trip_row, visible)], remainder)), len(rows))

    events = tuple(event for event in bundle.events
                   if in_range(event.date, bounds) and _person_allows_event(event, person, actor_key))
    selected = []
    for event in events:
        all_documents = documents_for_context(bundle, event)
        documents = tuple(document for document in all_documents
                          if semantic_type is None or document.semantic_type == semantic_type)
        if (query_type == "events_without_documents" and not all_documents) or (
                query_type in {"events_with_documents", "events_with_document_type"} and documents):
            selected.append(event)
    selected.sort(key=lambda event: (event.date, event.start_time or datetime.min.time(),
                                     event.canonical_parent_type, event.canonical_parent_id))
    if not selected:
        return ContextQueryResult("not_found", "Подходящих событий не найдено.", 0)
    visible, remainder = limited(tuple(selected))
    heading = "События без документов:" if query_type == "events_without_documents" else "События с документами:"
    return ContextQueryResult("found", "\n".join(_with_remainder([heading, "", *map(_event_row, visible)], remainder)), len(selected))


def _semantic_label(semantic_type: str) -> str:
    return {"transport_ticket": "транспортный билет", "voucher": "ваучер",
            "reservation": "бронь", "insurance": "страховка", "other": "документ"}[semantic_type]


def query_context(data: dict[str, Any], *, actor_key: str, now: datetime, timezone: str,
                  query_type: str, destination: str | None = None,
                  transport_type: str | None = None, target: str | None = None,
                  date_expression: str | None = None, person: str | None = None,
                  semantic_type: str | None = None,
                  context_id: str | None = None, context_domain: str | None = None) -> ContextQueryResult:
    """Resolve facts locally; provider-derived arguments never contain actor or facts."""
    # ``person`` is intentionally ignored for authorization: actor scope is
    # derived by the application and cannot be widened by provider output.
    cross_queries = {"events_during_trip", "events_on_trip_arrival", "trips_missing_documents",
                     "trips_missing_return", "events_with_documents", "events_without_documents",
                     "events_with_document_type"}
    event_query = query_type in {"events", "next_event", "event_time", "event_date", "event_place", "event_documents"}
    include_past = query_type == "upcoming_brief" or not (event_query or query_type in cross_queries)
    bundle = build_context_bundle(data, actor_key, now, timezone, include_past=include_past)
    if query_type == "upcoming_brief":
        lower, upper = resolve_brief_range(date_expression, now, timezone)
        brief = build_upcoming_brief(bundle, actor_key=actor_key, date_from=lower, date_to=upper, person=person)
        return ContextQueryResult("found", render_upcoming_brief(brief, today=zoned_now(timezone, now).date()),
                                  len(brief.events) + brief.event_remainder + len(brief.trips),
                                  upcoming_brief=brief)
    if query_type in cross_queries:
        return _cross_query(bundle, query_type=query_type, destination=destination,
                            transport_type=transport_type, date_expression=date_expression,
                            person=person, semantic_type=semantic_type, actor_key=actor_key,
                            now=now, timezone=timezone)
    if event_query:
        return _event_query(bundle, query_type, target, date_expression, now, timezone,
                            context_id if context_domain == "event" else None)
    trips = (tuple(trip for trip in bundle.trips if trip.context_id == context_id)
             if context_domain == "trip" and context_id else
             find_trip_by_destination(bundle, destination) if destination else find_trip_contexts(bundle))
    if query_type == "trip_briefing":
        bounds = date_bounds(date_expression, now, timezone)
        trips = tuple(trip for trip in trips if in_range(trip.trip_start.date(), bounds))
    if transport_type:
        trips = tuple(trip for trip in trips if any(
            row.transport_type == transport_type for row in documents_for_context(bundle, trip)
            if row.semantic_type == "transport_ticket"))
    if not trips:
        suffix = f" в {destination}" if destination else ""
        return ContextQueryResult("not_found", f"Не нашёл сохранённую поездку{suffix}.", 0)
    if len(trips) != 1:
        rows, remainder = limited(tuple(sorted(trips, key=lambda row: (row.trip_start, row.context_id))))
        lines = _with_remainder(["Нашёл несколько подходящих поездок:", "", *map(_trip_row, rows)], remainder)
        return ContextQueryResult("ambiguous", "\n".join([*lines, "", "Какую ты имеешь в виду?"]), len(trips))
    trip = trips[0]
    if query_type == "trip_briefing":
        briefing = build_trip_briefing(bundle, trip)
        if briefing is None:
            return ContextQueryResult("missing", "Поездку нашёл, но транспортные данные недоступны.", 1, trip)
        return ContextQueryResult("found", render_trip_briefing(briefing), 1, trip, briefing=briefing)
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
