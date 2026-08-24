"""Read-only resolution and rendering for explicit event assistant actions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from bot.services.context_engine import (
    ContextBundle,
    EventContext,
    TripContext,
    build_context_bundle,
    documents_for_context,
    find_event_context,
)


@dataclass(frozen=True, slots=True)
class EventActionContext:
    bundle: ContextBundle
    event: EventContext
    trip: TripContext | None
    document_count: int


@dataclass(frozen=True, slots=True)
class TripActionContext:
    """A freshly rebuilt, actor-visible canonical trip projection."""
    bundle: ContextBundle
    trip: TripContext
    documents: tuple
    linked_events: tuple[EventContext, ...]


def resolve_trip_action_context(data: dict, *, actor_key: str, trip_context_id: str,
                                now: datetime, timezone: str) -> TripActionContext | None:
    bundle = build_context_bundle(data, actor_key, now, timezone, include_past=True)
    trip = next((row for row in bundle.trips if row.context_id == trip_context_id), None)
    if trip is None:
        return None
    event_ids = set(trip.linked_event_ids)
    events = tuple(event for event in bundle.events if event.context_id in event_ids)
    # A trip owns its tickets and also the other canonical documents of its
    # visible linked events (voucher, insurance, reservation, and so on).
    identities = set(trip.linked_attachment_ids)
    parents = {(event.canonical_parent_type, event.canonical_parent_id) for event in events}
    documents = tuple(document for document in bundle.documents
                      if document.attachment_id in identities
                      or (document.parent_type, document.parent_id) in parents)
    return TripActionContext(bundle, trip, documents, events)


def select_event_trip(event: EventContext, trips: tuple[TripContext, ...]) -> TripContext | None:
    """Conservatively select one linked segment using notification semantics."""
    linked = tuple(trip for trip in trips if event.context_id in trip.linked_event_ids)
    exact_arrivals = tuple(trip for trip in linked if trip.arrival_date == event.date)
    if exact_arrivals:
        return exact_arrivals[0] if len(exact_arrivals) == 1 else None
    recent = tuple(trip for trip in linked if trip.arrival_date is not None
                   and timedelta(0) <= event.date - trip.arrival_date <= timedelta(days=3))
    if recent:
        closest_day = max(trip.arrival_date for trip in recent if trip.arrival_date is not None)
        closest = tuple(trip for trip in recent if trip.arrival_date == closest_day)
        return closest[0] if len(closest) == 1 else None
    exact_departures = tuple(trip for trip in linked if trip.departure_date == event.date)
    if exact_departures:
        return exact_departures[0] if len(exact_departures) == 1 else None
    return linked[0] if len(linked) == 1 else None


def resolve_event_action_context(data: dict, *, actor_key: str, parent_type: str,
                                 parent_id: str, now: datetime, timezone: str) -> EventActionContext | None:
    bundle = build_context_bundle(data, actor_key, now, timezone, include_past=True)
    event = find_event_context(bundle, parent_type, parent_id)
    if event is None:
        return None
    documents = documents_for_context(bundle, event)
    return EventActionContext(bundle, event, select_event_trip(event, bundle.trips), len(documents))


def visible_actions(value: EventActionContext) -> tuple[str, ...]:
    # Weather Context explicitly permits Moscow fallback for a stored event.
    actions = ["weather"]
    if value.document_count:
        actions.append("docs")
    if value.trip is not None:
        actions.append("trip")
    actions.append("overview")
    return tuple(actions)


_MONTHS = ("", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
           "августа", "сентября", "октября", "ноября", "декабря")


def _day(value) -> str:
    return f"{value.day} {_MONTHS[value.month]}"


def render_trip(trip: TripContext) -> str:
    lines = ["🚆 Поездка", ""]
    if trip.departure_date:
        moment = _day(trip.departure_date)
        if trip.departure_time:
            moment += f" · {trip.departure_time:%H:%M}"
        lines += ["Туда:", moment]
        if trip.origin and trip.destination:
            lines.append(f"{trip.origin} → {trip.destination}")
    if trip.arrival_date:
        moment = _day(trip.arrival_date)
        if trip.arrival_time:
            moment += f" · {trip.arrival_time:%H:%M}"
        lines += ["", "Прибытие:", moment]
    return "\n".join(lines)


def _moment(day, clock) -> str | None:
    if not day:
        return None
    return _day(day) + (f" · {clock:%H:%M}" if clock else "")


def render_trip_route(trip: TripContext) -> str:
    lines = ["🚆 Маршрут"]
    departure = _moment(trip.departure_date, trip.departure_time)
    if departure:
        lines += ["", "Отправление:", departure]
    route = " → ".join(value for value in (trip.origin, trip.destination) if value)
    if route:
        lines += ["", route]
    arrival = _moment(trip.arrival_date, trip.arrival_time)
    if arrival:
        lines += ["", "Прибытие:", arrival]
    return "\n".join(lines)


def render_trip_card(value: TripActionContext) -> str:
    trip = value.trip
    heading = f"🧳 Поездка в {trip.city_hint or trip.destination}" if (trip.city_hint or trip.destination) else "🧳 Поездка"
    lines = [heading]
    route = render_trip_route(trip).splitlines()[1:]
    if route:
        lines += route
    if len(value.linked_events) == 1:
        event = value.linked_events[0]
        lines += ["", "Связано с:", f"{event.title} · {_day(event.date)}"]
    elif value.linked_events:
        lines += ["", f"Связано событий: {len(value.linked_events)}"]
    lines += ["", f"Документы: {len(value.documents)}"]
    return "\n".join(lines)


def render_trip_overview(value: TripActionContext) -> str:
    return "🗓 Что известно\n\n" + "\n".join(render_trip_card(value).splitlines()[1:]).lstrip()


def trip_weather_target(trip: TripContext):
    """Return only explicit trip weather coordinates, never event fallback."""
    return (trip.city_hint or trip.destination or None,
            trip.arrival_date or trip.departure_date)


def render_overview(value: EventActionContext) -> str:
    event, trip = value.event, value.trip
    when = _day(event.date)
    if event.start_time:
        when += f" · {event.start_time:%H:%M}"
    lines = ["🗓 Что известно", "", f"Когда: {when}"]
    if event.location_text:
        lines.append(f"Где: {event.location_text}")
    if trip:
        route = " → ".join(part for part in (trip.origin, trip.destination) if part)
        if route:
            lines.append(f"Поездка: {route}")
        if trip.arrival_date:
            arrival = _day(trip.arrival_date)
            if trip.arrival_time:
                arrival += f" · {trip.arrival_time:%H:%M}"
            lines.append(f"Прибытие: {arrival}")
    lines.append(f"Документы: {value.document_count}")
    return "\n".join(lines)
