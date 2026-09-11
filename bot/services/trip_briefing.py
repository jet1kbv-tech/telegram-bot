"""Pure assembly and rendering of a compact canonical trip briefing."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from bot.services.context_engine import ContextBundle, DocumentContext, EventContext, TripContext
from bot.services.cross_context_queries import direction_document, events_overlapping_trip, trip_documents, trip_interval
from bot.services.event_attachment_display import transport_icon_label
from bot.services.weather import WeatherProvider
from bot.services.weather_context import format_forecast

PLAN_LIMIT = 5
MONTHS = ("", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")


@dataclass(frozen=True, slots=True)
class BriefingDocument:
    attachment_id: str
    label: str


@dataclass(frozen=True, slots=True)
class TripBriefing:
    trip: TripContext
    outbound: DocumentContext
    returning: DocumentContext | None
    interval: tuple[datetime, datetime] | None
    plans: tuple[EventContext, ...]
    plans_remainder: int
    documents: tuple[BriefingDocument, ...]

    @property
    def weather_target(self) -> tuple[str, date, date] | None:
        if self.interval is None:
            return None
        location = self.trip.city_hint or self.trip.destination
        return (location, self.interval[0].date(), self.interval[1].date()) if location else None


def _document_label(document: DocumentContext, trip: TripContext) -> str:
    direction = dict(trip.directions).get(document.attachment_id)
    if document.semantic_type == "transport_ticket":
        if direction == "outbound":
            return "Билет туда"
        if direction == "return":
            return "Билет обратно"
        return transport_icon_label(document.transport_type)[1]
    return {"voucher": "Ваучер", "reservation": "Бронь", "insurance": "Страховка"}.get(
        document.semantic_type, "Документ")


def build_trip_briefing(bundle: ContextBundle, trip: TripContext) -> TripBriefing | None:
    """Build only from the supplied fresh actor-scoped canonical projection."""
    outbound = direction_document(bundle, trip, "outbound")
    if outbound is None:
        return None
    overlapping = events_overlapping_trip(bundle, trip) or ()
    seen: set[str] = set()
    documents = []
    for document in trip_documents(bundle, trip):
        if not document.attachment_id or document.attachment_id in seen:
            continue
        seen.add(document.attachment_id)
        documents.append(BriefingDocument(document.attachment_id, _document_label(document, trip)))
    return TripBriefing(trip, outbound, direction_document(bundle, trip, "return"), trip_interval(bundle, trip),
                        overlapping[:PLAN_LIMIT], max(0, len(overlapping) - PLAN_LIMIT), tuple(documents))


def _day(value: date) -> str:
    return f"{value.day} {MONTHS[value.month]}"


def _range(interval: tuple[datetime, datetime] | None) -> str | None:
    if interval is None:
        return None
    start, end = interval[0].date(), interval[1].date()
    if start == end:
        return _day(start)
    if start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS[start.month]}"
    return f"{_day(start)} — {_day(end)}"


def _segment(label: str, document: DocumentContext) -> list[str]:
    icon, _ = transport_icon_label(document.transport_type)
    lines = [f"{icon} {label}"]
    route = " → ".join(value for value in (document.origin, document.destination) if value)
    if route:
        lines.append(route)
    departure = _day(document.departure_date) if document.departure_date else None
    if departure and document.departure_time:
        departure += f", {document.departure_time:%H:%M}"
    arrival = _day(document.arrival_date) if document.arrival_date else None
    if arrival and document.arrival_time:
        arrival += f", {document.arrival_time:%H:%M}"
    if departure and arrival:
        lines.append(f"{departure} → {arrival}")
    elif departure or arrival:
        lines.append(departure or arrival or "")
    return lines


def render_trip_briefing(value: TripBriefing) -> str:
    lines = [f"📍 {value.trip.city_hint or value.trip.destination}"]
    if date_range := _range(value.interval):
        lines.append(date_range)
    lines += ["", *_segment("Туда", value.outbound), ""]
    if value.returning:
        lines += _segment("Обратно", value.returning)
    else:
        lines += ["↩️ Обратно", "Обратный транспорт не прикреплён"]
    if value.plans:
        lines += ["", "📅 Планы"]
        for event in value.plans:
            clock = f", {event.start_time:%H:%M}" if event.start_time else ""
            lines.append(f"• {_day(event.date)}{clock} — {event.title or 'Событие'}")
        if value.plans_remainder:
            lines.append(f"И ещё {value.plans_remainder}…")
    if value.documents:
        lines += ["", "📎 Документы", *[f"• {item.label}" for item in value.documents]]
    return "\n".join(lines)


async def render_weather_enrichment(value: TripBriefing, provider: WeatherProvider | None,
                                    *, today: date | None = None) -> str | None:
    """Return optional weather text; every provider/parsing failure preserves core output."""
    target = value.weather_target
    if provider is None or target is None:
        return None
    location, date_from, date_to = target
    horizon = getattr(provider, "horizon_days", None)
    current = today or date.today()
    if isinstance(horizon, int) and (date_from < current or date_to > current + timedelta(days=horizon)):
        return None
    try:
        forecast = await provider.get_forecast(location, date_from, date_to)
        if not forecast.days or forecast.days[0].date != date_from or forecast.days[-1].date != date_to:
            return None
        body = "\n".join(format_forecast(forecast, include_advice=False).splitlines()[1:]).lstrip()
        return f"🌤 Погода\n{body}" if body else None
    except Exception:
        return None
