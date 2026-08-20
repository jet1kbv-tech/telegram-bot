"""Local context resolution and deterministic weather presentation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from bot.services.context_engine import EventContext, build_context_bundle, find_trip_by_destination, find_trip_contexts, normalize_location_text
from bot.services.weather import WeatherForecast, WeatherHorizonUnavailable, WeatherLocationUnresolved, WeatherProvider

UMBRELLA_PROBABILITY = 60
COLD_MAX_TEMPERATURE = 12
MAX_DISPLAY_DAYS = 6
MONTHS = ("", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")


@dataclass(frozen=True, slots=True)
class WeatherContextResult:
    outcome: str
    text: str
    candidate_count: int = 0


def _matches(value: str, target: str) -> bool:
    left, right = normalize_location_text(value), normalize_location_text(target)
    return bool(right) and (right in left or left in right)


def _event_candidates(events: tuple[EventContext, ...], target: str | None) -> tuple[EventContext, ...]:
    if not target: return ()
    return tuple(event for event in events if _matches(event.title, target))


def advice(forecast: WeatherForecast) -> tuple[str, ...]:
    result = []
    if any(day.precipitation_probability is not None and day.precipitation_probability >= UMBRELLA_PROBABILITY for day in forecast.days):
        result.append("Лучше взять зонт.")
    if any(day.max_temperature <= COLD_MAX_TEMPERATURE for day in forecast.days):
        result.append("Стоит взять что-то потеплее.")
    return tuple(result)


def _day(value: date) -> str: return f"{value.day} {MONTHS[value.month]}"
def _temp(value: float) -> str: return f"{value:+.0f}"


def format_forecast(value: WeatherForecast, *, context_note: str | None = None, include_advice: bool = True) -> str:
    days = value.days[:MAX_DISPLAY_DAYS]
    if not days: return "Сейчас не получилось получить прогноз. Попробуй чуть позже."
    heading_date = _day(days[0].date) if len(days) == 1 else f"{_day(days[0].date)} — {_day(value.days[-1].date)}"
    lines = [f"🌦 {value.location_label} · {heading_date}"]
    if context_note: lines += ["", context_note]
    lines.append("")
    for item in days:
        rain = f" · осадки {item.precipitation_probability}%" if item.precipitation_probability is not None else ""
        lines.append(f"{_day(item.date)} · {_temp(item.min_temperature)}…{_temp(item.max_temperature)} °C · {item.condition}{rain}")
    if len(value.days) > MAX_DISPLAY_DAYS: lines.append(f"…ещё {len(value.days) - MAX_DISPLAY_DAYS} дн.")
    tips = advice(value) if include_advice else ()
    if tips: lines += ["", "💡 " + " ".join(tips)]
    return "\n".join(lines)


async def query_weather_context(data: dict[str, Any], *, actor_key: str, now: datetime, timezone: str,
                                provider: WeatherProvider, weather_scope: str, target: str | None,
                                location: str | None, explicit_date: date | None,
                                include_advice: bool) -> WeatherContextResult:
    """Send provider only a location and date range, never personal context."""
    bundle = build_context_bundle(data, actor_key, now, timezone, include_past=True)
    chosen_location, start, end, note = location, explicit_date, explicit_date, None
    if weather_scope == "current":
        start = end = now.date()
    if weather_scope in {"arrival", "trip"}:
        trips = find_trip_by_destination(bundle, target or location) if (target or location) else find_trip_contexts(bundle)
        if not trips: return WeatherContextResult("not_found", "Не нашёл сохранённую поездку.")
        if len(trips) != 1: return WeatherContextResult("ambiguous", "Нашёл несколько подходящих поездок. Уточни, какую выбрать.", len(trips))
        trip = trips[0]
        chosen_location = trip.city_hint or trip.destination
        if weather_scope == "arrival":
            start = end = trip.arrival_date
            note = f"На момент вашего прибытия — {_day(start)}." if start else None
        else:
            start, end = trip.trip_start.date(), trip.trip_end.date()
            note = "По датам сохранённой поездки."
    elif weather_scope == "event":
        events = _event_candidates(bundle.events, target)
        if not events: return WeatherContextResult("not_found", "Не нашёл такое сохранённое событие.")
        if len(events) != 1: return WeatherContextResult("ambiguous", "Нашёл несколько подходящих событий. Уточни, какое выбрать.", len(events))
        event = events[0]
        chosen_location = event.location_text or location or "Москва"
        start, end, note = event.date, event.end_date or event.date, "По датам сохранённого события."
    if not chosen_location: return WeatherContextResult("location_missing", "Не смог определить место для прогноза.")
    if not start: return WeatherContextResult("date_missing", "Не смог определить дату для прогноза.")
    try:
        forecast = await provider.get_forecast(chosen_location, start, end)
    except WeatherHorizonUnavailable:
        return WeatherContextResult("horizon", "Прогноз на эту дату ещё недоступен. Обычно точный прогноз появляется ближе к поездке.")
    except WeatherLocationUnresolved:
        return WeatherContextResult("location_unresolved", "Нашёл контекст, но не смог определить место.")
    return WeatherContextResult("found", format_forecast(forecast, context_note=note, include_advice=include_advice), 1)
