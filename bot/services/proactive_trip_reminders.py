"""Deterministic actor-scoped proactive reminders for canonical trip segments."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.context_engine import TripContext, build_context_bundle, documents_for_context
from bot.services.weather import WeatherProvider

logger = logging.getLogger(__name__)
REMINDER_OFFSETS = {"trip_24h": timedelta(hours=24), "trip_2h": timedelta(hours=2)}
_MONTHS = ("", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
           "августа", "сентября", "октября", "ноября", "декабря")


@dataclass(frozen=True, slots=True)
class TripReminder:
    reminder_type: str
    marker: str
    text: str
    keyboard: InlineKeyboardMarkup


def departure_datetime(trip: TripContext, timezone: str) -> datetime | None:
    if not trip.departure_date or not trip.departure_time:
        return None
    return datetime.combine(trip.departure_date, trip.departure_time, ZoneInfo(timezone))


def reminder_marker(actor: str, trip: TripContext, reminder_type: str, departure: datetime) -> str:
    raw = "\x1f".join((actor, trip.context_id, reminder_type, departure.isoformat()))
    return hashlib.sha256(raw.encode()).hexdigest()


def eligible_type(trip: TripContext, now: datetime, timezone: str,
                  grace: timedelta) -> tuple[tuple[str, datetime, str], ...]:
    departure = departure_datetime(trip, timezone)
    if departure is None:
        return ()
    local_now = now.astimezone(ZoneInfo(timezone)) if now.tzinfo else now.replace(tzinfo=ZoneInfo(timezone))
    return tuple((kind, departure, kind) for kind, offset in REMINDER_OFFSETS.items()
                 if departure - offset <= local_now < departure - offset + grace)


def _day(value) -> str:
    return f"{value.day} {_MONTHS[value.month]}"


def _route(trip: TripContext) -> str:
    return " → ".join(value for value in (trip.origin, trip.destination) if value)


def _weather_text(forecast) -> str:
    if not forecast.days:
        return ""
    item = forecast.days[0]
    rain = f" · осадки {item.precipitation_probability}%" if item.precipitation_probability is not None else ""
    return (f"🌦 {forecast.location_label} · {_day(item.date)}\n"
            f"{item.min_temperature:+.0f}…{item.max_temperature:+.0f} °C · {item.condition}{rain}")


def render_reminder(kind: str, trip: TripContext, documents: tuple, weather: str = "") -> str:
    route = _route(trip)
    if kind == "trip_2h":
        line = f"{trip.departure_time:%H:%M}" + (f" · {route}" if route else "")
        lines = ["🚆 Через 2 часа поезд", "", line]
        if trip.arrival_time:
            lines.append(f"Прибытие: {trip.arrival_time:%H:%M}")
    else:
        lines = ["🧳 Завтра поездка", "", f"🚆 {_day(trip.departure_date)} · {trip.departure_time:%H:%M}"]
        if route:
            lines.append(route)
        if trip.arrival_date:
            arrival = _day(trip.arrival_date) + (f" · {trip.arrival_time:%H:%M}" if trip.arrival_time else "")
            lines += ["", "Прибытие:", arrival]
        if weather:
            lines += ["", weather]
    if documents:
        if len(documents) == 1 and documents[0].semantic_type == "transport_ticket":
            label = "📎 Билет сохранён"
        else:
            label = f"📎 Документы: {len(documents)}"
        lines += ["", label]
    return "\n".join(lines)


def reminder_keyboard(trip: TripContext, documents: tuple, linked_events: tuple,
                      *, include_weather: bool) -> InlineKeyboardMarkup:
    buttons = []
    if documents:
        buttons.append(InlineKeyboardButton("📎 Документы", callback_data=f"ctx:trip:docs:{trip.context_id}"))
    buttons.append(InlineKeyboardButton("🚆 Поездка", callback_data=f"ctx:trip:card:{trip.context_id}"))
    if include_weather:
        buttons.append(InlineKeyboardButton("🌦 Погода", callback_data=f"ctx:trip:weather:{trip.context_id}"))
    if len(linked_events) == 1:
        event = linked_events[0]
        callback = (f"view|afisha|{event.canonical_parent_id}|0" if event.canonical_parent_type == "afisha"
                    else f"cal_view|{trip.actor_scope}|{event.canonical_parent_id}|0")
        buttons.append(InlineKeyboardButton("📅 Событие", callback_data=callback))
    return InlineKeyboardMarkup([buttons[index:index + 2] for index in range(0, len(buttons), 2)])


async def scan_trip_reminders(*, storage, bot, actors: dict[str, dict[str, Any]],
                              timezone: str, grace: timedelta, now: datetime,
                              weather_provider: WeatherProvider | None = None) -> dict[str, int]:
    """Load once, isolate candidates/actors, and mark only accepted sends."""
    data = storage.load()
    chats = data.get("meta", {}).get("user_chats", {})
    delivered = set(data.get("meta", {}).get("trip_reminder_deliveries", []))
    counts = {"candidates": 0, "sent": 0, "failed": 0, "deduped": 0}
    for username, profile in actors.items():
        chat_id, actor = chats.get(username), str(profile.get("wishlist_owner") or "")
        if not chat_id or not actor:
            continue
        try:
            bundle = build_context_bundle(data, actor, now, timezone, include_past=True)
        except Exception:
            counts["failed"] += 1
            logger.warning("trip_reminder actor=failed", exc_info=True)
            continue
        for trip in bundle.trips:
            try:
                for kind, departure, _ in eligible_type(trip, now, timezone, grace):
                    counts["candidates"] += 1
                    marker = reminder_marker(actor, trip, kind, departure)
                    if marker in delivered:
                        counts["deduped"] += 1
                        continue
                    documents = documents_for_context(bundle, trip)
                    event_ids = set(trip.linked_event_ids)
                    linked_events = tuple(event for event in bundle.events if event.context_id in event_ids)
                    weather = ""
                    location = day = None
                    if kind == "trip_24h" and weather_provider is not None:
                        location, day = trip.city_hint or trip.destination, trip.arrival_date or trip.departure_date
                        if location and day:
                            try:
                                weather = _weather_text(await weather_provider.get_forecast(location, day, day))
                            except Exception:
                                logger.info("trip_reminder weather=unavailable")
                    text = render_reminder(kind, trip, documents, weather)
                    keyboard = reminder_keyboard(trip, documents, linked_events,
                                                 include_weather=kind == "trip_24h" and bool(location and day))
                    try:
                        await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
                    except Exception:
                        counts["failed"] += 1
                        logger.warning("trip_reminder delivery=failed")
                        continue
                    delivered.add(marker)
                    data.setdefault("meta", {})["trip_reminder_deliveries"] = sorted(delivered)
                    try:
                        storage.save(data)
                    except Exception:
                        # The send succeeded; retain the in-cycle marker and continue. A
                        # restart may duplicate it because durable acknowledgement failed.
                        counts["failed"] += 1
                        logger.error("trip_reminder marker=failed")
                        continue
                    counts["sent"] += 1
                    logger.info("trip_reminder sent type=%s", kind)
            except Exception:
                counts["failed"] += 1
                logger.warning("trip_reminder candidate=failed", exc_info=True)
    logger.info("trip_reminder cycle candidates=%s sent=%s failed=%s deduped=%s", *counts.values())
    return counts
