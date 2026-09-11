"""Pure assembly and rendering for the bounded upcoming-life brief."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from bot.services.context_engine import ContextBundle, DocumentContext, EventContext, TripContext
from bot.services.cross_context_queries import direction_document, event_bounds, trip_documents, trip_interval
from bot.services.event_attachment_display import transport_icon_label
from bot.services.nl_dates import resolve_date_range, zoned_now
from bot.services.weather import WeatherProvider
from bot.services.weather_context import format_forecast

EVENT_LIMIT = 8
DEFAULT_HORIZON_DAYS = 7
MONTHS = ("", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")
WEEKDAYS = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")


@dataclass(frozen=True, slots=True)
class DocumentSignal:
    subject: str
    label: str


@dataclass(frozen=True, slots=True)
class UpcomingTrip:
    trip: TripContext
    interval: tuple[datetime, datetime]
    transport_type: str | None


@dataclass(frozen=True, slots=True)
class UpcomingBrief:
    date_from: date
    date_to: date
    events: tuple[EventContext, ...]
    event_remainder: int
    trips: tuple[UpcomingTrip, ...]
    document_signals: tuple[DocumentSignal, ...]
    weather_target: tuple[str, date, date] | None = None


def resolve_brief_range(expression: str | None, now: datetime, timezone: str) -> tuple[date, date]:
    if expression:
        lower, upper = resolve_date_range(expression, now=now, timezone=timezone)
        return date.fromisoformat(lower), date.fromisoformat(upper)
    today = zoned_now(timezone, now).date()
    return today, today + timedelta(days=DEFAULT_HORIZON_DAYS - 1)


def _allows_event(event: EventContext, person: str | None, actor: str) -> bool:
    return person in {None, "self", actor} or event.is_shared


def _allows_trip(bundle: ContextBundle, trip: TripContext, person: str | None, actor: str) -> bool:
    if person in {None, "self", actor}:
        return True
    linked = set(trip.linked_event_ids)
    return bool(linked) and all(event.is_shared for event in bundle.events if event.context_id in linked)


def _overlaps(start: datetime, end: datetime, lower: date, upper: date) -> bool:
    return start.date() <= upper and end.date() >= lower


def _trip_signal(bundle: ContextBundle, item: UpcomingTrip) -> DocumentSignal | None:
    documents = trip_documents(bundle, item.trip)
    outbound = direction_document(bundle, item.trip, "outbound")
    returning = direction_document(bundle, item.trip, "return")
    if outbound and returning:
        label = "билеты туда и обратно"
    elif outbound:
        label = "билет туда"
    elif returning:
        label = "билет обратно"
    elif documents:
        label = "есть вложение"
    else:
        return None
    return DocumentSignal(item.trip.city_hint or item.trip.destination, label)


def build_upcoming_brief(bundle: ContextBundle, *, actor_key: str, date_from: date, date_to: date,
                         person: str | None = None) -> UpcomingBrief:
    """Select only actor-visible canonical facts; provider scope can only narrow."""
    trips = []
    for trip in bundle.trips:
        interval = trip_interval(bundle, trip)
        if interval and _allows_trip(bundle, trip, person, actor_key) and _overlaps(*interval, date_from, date_to):
            outbound = direction_document(bundle, trip, "outbound")
            trips.append(UpcomingTrip(trip, interval, outbound.transport_type if outbound else None))
    trips.sort(key=lambda item: (item.interval[0], item.trip.context_id))

    linked_event_ids = {identity for item in trips for identity in item.trip.linked_event_ids}
    unique: dict[tuple[str, str], EventContext] = {}
    for event in bundle.events:
        identity = (event.canonical_parent_type, event.canonical_parent_id)
        if (event.context_id not in linked_event_ids and _allows_event(event, person, actor_key)
                and _overlaps(*event_bounds(event), date_from, date_to)):
            unique.setdefault(identity, event)
    events = sorted(unique.values(), key=lambda event: (
        event_bounds(event)[0], event.canonical_parent_type, event.canonical_parent_id))

    signals = [signal for item in trips if (signal := _trip_signal(bundle, item))]
    trip_parents = {(document.parent_type, document.parent_id)
                    for item in trips for document in trip_documents(bundle, item.trip)}
    documents_by_parent: dict[tuple[str, str], list[DocumentContext]] = {}
    for document in bundle.documents:
        documents_by_parent.setdefault((document.parent_type, document.parent_id), []).append(document)
    for event in events:
        parent = (event.canonical_parent_type, event.canonical_parent_id)
        if parent not in trip_parents and documents_by_parent.get(parent):
            signals.append(DocumentSignal(event.title or "Событие", "есть вложение"))

    weather_target = None
    if len(trips) == 1:
        item, location = trips[0], trips[0].trip.city_hint or trips[0].trip.destination
        if location:
            weather_target = (location, max(date_from, item.interval[0].date()),
                              min(date_to, item.interval[1].date()))
    return UpcomingBrief(date_from, date_to, tuple(events[:EVENT_LIMIT]), max(0, len(events) - EVENT_LIMIT),
                         tuple(trips), tuple(signals), weather_target)


def _day(value: date) -> str:
    return f"{value.day} {MONTHS[value.month]}"


def _range(start: date, end: date) -> str:
    if start == end:
        return _day(start)
    if start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS[start.month]}"
    return f"{_day(start)} — {_day(end)}"


def render_upcoming_brief(value: UpcomingBrief, *, today: date) -> str:
    lines = ["🗓 Ближайшие планы", _range(value.date_from, value.date_to)]
    current = None
    for event in value.events:
        if event.date != current:
            current = event.date
            heading = ("Сегодня" if current == today else "Завтра" if current == today + timedelta(days=1)
                       else f"{WEEKDAYS[current.weekday()]}, {_day(current)}")
            lines += ["", heading]
        clock = f"{event.start_time:%H:%M} — " if event.start_time else ""
        lines.append(f"• {clock}{event.title or 'Событие'}")
    if value.event_remainder:
        lines.append(f"И ещё {value.event_remainder}…")
    if value.trips:
        lines += ["", "🚆 Поездки"]
        for item in value.trips:
            icon = transport_icon_label(item.transport_type)[0]
            lines.append(f"• {icon} {item.trip.city_hint or item.trip.destination} · "
                         f"{_range(item.interval[0].date(), item.interval[1].date())}")
    if value.document_signals:
        lines += ["", "📎 Документы"]
        lines.extend(f"• {signal.subject} — {signal.label}" for signal in value.document_signals)
    if not value.events and not value.trips:
        lines += ["", "На этот период ничего не запланировано."]
    return "\n".join(lines)


async def render_upcoming_weather(value: UpcomingBrief, provider: WeatherProvider | None,
                                  *, today: date | None = None) -> str | None:
    target = value.weather_target
    if provider is None or target is None:
        return None
    location, lower, upper = target
    current, horizon = today or date.today(), getattr(provider, "horizon_days", None)
    if lower > upper or (isinstance(horizon, int) and
                         (lower < current or upper > current + timedelta(days=horizon))):
        return None
    try:
        forecast = await provider.get_forecast(location, lower, upper)
        if not forecast.days or forecast.days[0].date != lower or forecast.days[-1].date != upper:
            return None
        body = "\n".join(format_forecast(forecast, include_advice=False).splitlines()[1:]).lstrip()
        return f"🌤 Погода\n{body}" if body else None
    except Exception:
        return None
