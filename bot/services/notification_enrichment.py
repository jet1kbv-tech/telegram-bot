"""Deterministic optional context for an already scheduled notification."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from bot.services.context_engine import EventContext, TripContext, build_context_bundle, find_event_context
from bot.services.weather import WeatherError, WeatherForecast, WeatherHorizonUnavailable, WeatherProvider
from bot.services.weather_context import MONTHS, advice


@dataclass(frozen=True, slots=True)
class NotificationEnrichment:
    event_context: EventContext | None
    trip_context: TripContext | None = None
    transport_type: str | None = None
    departure: datetime | None = None
    arrival: datetime | None = None
    weather_forecast: WeatherForecast | None = None
    weather_date: date | None = None
    weather_location: str | None = None
    advice: tuple[str, ...] = ()
    enrichment_reasons: tuple[str, ...] = ()
    weather_attempted: bool = False


def _stamp(day: date | None, clock: time | None) -> datetime | None:
    return datetime.combine(day, clock) if day and clock else None


def _linked_trip(event: EventContext, trips: tuple[TripContext, ...]) -> TripContext | None:
    linked = tuple(trip for trip in trips if event.context_id in trip.linked_event_ids)
    return linked[0] if len(linked) == 1 else None


async def build_notification_context(
    data: dict[str, Any], *, event_id: str, actor_key: str, now: datetime,
    timezone: str, weather_provider: WeatherProvider,
) -> NotificationEnrichment:
    """Resolve one canonical Afisha event without LLM or storage mutation.

    Provider input is restricted to the selected location and one date.  A
    provider exposing ``horizon_days`` is rejected locally before any request.
    Other provider failures intentionally propagate to the scheduler's optional
    enrichment boundary, where the original reminder remains sendable.
    """
    bundle = build_context_bundle(data, actor_key, now, timezone, include_past=True)
    event = find_event_context(bundle, "afisha", event_id)
    if event is None:
        return NotificationEnrichment(None, enrichment_reasons=("event_missing",))

    trip = _linked_trip(event, bundle.trips)
    transport_type = next((document.transport_type for document in bundle.documents
                           if trip and document.attachment_id in trip.linked_attachment_ids
                           and document.transport_type), None)
    departure = _stamp(trip.departure_date, trip.departure_time) if trip else None
    arrival = _stamp(trip.arrival_date, trip.arrival_time) if trip else None
    weather_date = trip.arrival_date if trip and trip.arrival_date else event.date
    location = (trip.city_hint or trip.destination) if trip else (event.location_text or "Москва")
    reasons = ["event_resolved"]
    if trip:
        reasons.append("trip_linked")

    horizon = getattr(weather_provider, "horizon_days", None)
    local_today = now.date()
    if isinstance(horizon, int) and (weather_date < local_today or weather_date > local_today + timedelta(days=horizon)):
        return NotificationEnrichment(event, trip, transport_type, departure, arrival, weather_date=weather_date,
            weather_location=location, enrichment_reasons=tuple(reasons + ["weather_horizon"]))

    try:
        forecast = await weather_provider.get_forecast(location, weather_date, weather_date)
    except WeatherHorizonUnavailable:
        return NotificationEnrichment(event, trip, transport_type, departure, arrival, weather_date=weather_date,
            weather_location=location, enrichment_reasons=tuple(reasons + ["weather_horizon"]), weather_attempted=True)
    except WeatherError:
        return NotificationEnrichment(event, trip, transport_type, departure, arrival, weather_date=weather_date,
            weather_location=location, enrichment_reasons=tuple(reasons + ["weather_failed"]), weather_attempted=True)
    if not forecast.days or forecast.days[0].date != weather_date:
        raise ValueError("weather forecast does not contain the requested day")
    return NotificationEnrichment(event, trip, transport_type, departure, arrival, forecast, weather_date, location,
        advice(forecast)[:2], tuple(reasons + ["weather_included"]), True)


def _day(value: date) -> str:
    return f"{value.day} {MONTHS[value.month]}"


def _moment(value: datetime) -> str:
    return f"{_day(value.date())} · {value:%H:%M}"


def render_notification_enrichment(value: NotificationEnrichment) -> str:
    """Render at most one travel block, one weather day and two advice lines."""
    blocks: list[str] = []
    if value.trip_context and (value.departure or value.arrival):
        icon = {"train": "🚆", "plane": "✈️", "flight": "✈️", "bus": "🚌"}.get(
            value.transport_type or "", "🧳")
        lines = []
        if value.departure:
            lines.append(f"{icon} Отправление: {_moment(value.departure)}")
        if value.arrival:
            lines.append(f"Прибытие: {_moment(value.arrival)}")
        blocks.append("\n".join(lines))
    if value.weather_forecast:
        day = value.weather_forecast.days[0]
        heading = f"🌦 {value.weather_forecast.location_label} · {_day(day.date)}"
        rain = f" · осадки {day.precipitation_probability}%" if day.precipitation_probability is not None else ""
        blocks.append(f"{heading}\n{day.min_temperature:+.0f}…{day.max_temperature:+.0f} °C · {day.condition}{rain}")
    if value.advice:
        blocks.append("\n".join(f"💡 {line}" for line in value.advice[:2]))
    return "\n\n".join(blocks)
