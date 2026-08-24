"""Telegram callbacks for deterministic contextual event actions."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import BOT_TIMEZONE
from bot.services.afisha_calendar_sync import build_afisha_projection_id
from bot.handlers.event_attachments import show_documents
from bot.services.contextual_actions import (
    EventActionContext,
    render_overview,
    render_trip_card,
    render_trip_overview,
    render_trip_route,
    resolve_event_action_context,
    resolve_event_action_context_id,
    resolve_trip_action_context,
    trip_weather_target,
    visible_actions,
)
from bot.services.weather import WeatherError, WeatherHorizonUnavailable, WeatherProvider
from bot.services.weather_context import format_forecast
from bot.states import SECTION
from bot.storage import storage
from bot.utils import get_wishlist_owner_by_user, remember_current_chat

logger = logging.getLogger(__name__)
_safe_edit_message: Callable[..., Awaitable[None]] | None = None
_weather_provider: WeatherProvider | None = None


def configure_contextual_action_handlers(*, safe_edit_message: Callable[..., Awaitable[None]],
                                         weather_provider: WeatherProvider) -> None:
    global _safe_edit_message, _weather_provider
    _safe_edit_message, _weather_provider = safe_edit_message, weather_provider


def _callback(action: str, parent_type: str, parent_id: str, page: int) -> str:
    kind = "a" if parent_type == "afisha" else "c"
    return f"ctx:event:{action}:{kind}:{parent_id}:{page}"


def trip_callback(action: str, trip_context_id: str) -> str:
    """Bounded callback containing only an action and canonical opaque ID."""
    return f"ctx:trip:{action}:{trip_context_id}"


def _trip_callback_with_source(action: str, trip_id: str, kind: str, event_id: str, page: int) -> str:
    return f"ctx:trip:{action}:{trip_id}:{kind}:{event_id}:{page}"


def _trips_callback(kind: str, event_id: str, page: int) -> str:
    return f"ctx:trips:{kind}:{event_id}:{page}"


def contextual_action_rows(data: dict, *, actor_key: str, parent_type: str,
                           parent_id: str, page: int, now: datetime | None = None,
                           source_kind: str | None = None,
                           source_parent_id: str | None = None) -> list[list[InlineKeyboardButton]]:
    if actor_key not in {"vova", "sasha"}:
        return []
    value = resolve_event_action_context(data, actor_key=actor_key, parent_type=parent_type,
        parent_id=parent_id, now=now or datetime.now(), timezone=BOT_TIMEZONE)
    if value is None:
        return []
    labels = {"weather": "🌦 Погода", "docs": "📎 Документы",
              "trip": "🚆 Поездки" if len(value.trips) > 1 else "🚆 Поездка",
              "overview": "🗓 Что известно"}
    callback_parent = "calendar" if source_kind == "p" else parent_type
    callback_id = source_parent_id or parent_id
    buttons = [InlineKeyboardButton(labels[action], callback_data=
               _callback(action, callback_parent, callback_id, page).replace(":c:", ":p:"))
               for action in visible_actions(value)]
    return [buttons[index:index + 2] for index in range(0, len(buttons), 2)]


def _back(parent_type: str, parent_id: str, page: int, actor_key: str) -> InlineKeyboardMarkup:
    callback = (f"view|afisha|{parent_id}|{page}" if parent_type == "afisha"
                else f"cal_view|{actor_key}|{parent_id}|{page}")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ К событию", callback_data=callback)],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])


def _trip_keyboard(value, *, back_callback: str = "menu:main", source: tuple[str, str, int] | None = None) -> InlineKeyboardMarkup:
    trip_id = value.trip.context_id
    callback = (lambda action: _trip_callback_with_source(action, trip_id, *source)) if source else (lambda action: trip_callback(action, trip_id))
    rows = [
        [InlineKeyboardButton("🌦 Погода", callback_data=callback("weather")),
         InlineKeyboardButton("📎 Документы", callback_data=callback("docs"))],
        [InlineKeyboardButton("🚆 Маршрут", callback_data=callback("route")),
         InlineKeyboardButton("🗓 Что известно", callback_data=callback("overview"))],
    ]
    if len(value.linked_events) == 1:
        event = value.linked_events[0]
        callback = (f"view|afisha|{event.canonical_parent_id}|0" if event.canonical_parent_type == "afisha"
                    else f"cal_view|{value.trip.actor_scope}|{event.canonical_parent_id}|0")
        rows.append([InlineKeyboardButton("📅 К событию", callback_data=callback)])
    rows += [[InlineKeyboardButton("⬅️ Назад", callback_data=back_callback)],
             [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]
    return InlineKeyboardMarkup(rows)


def _trip_selector(value: EventActionContext, kind: str, page: int,
                   actor_key: str) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["🚆 Поездки"]
    rows = []
    for index, trip in enumerate(value.trips, 1):
        route = " → ".join(part for part in (trip.origin, trip.destination) if part) or "Маршрут не указан"
        lines += ["", f"🚆 {index} · {route}"]
        departure = _selector_moment(trip.departure_date, trip.departure_time)
        arrival = _selector_moment(trip.arrival_date, trip.arrival_time)
        if departure:
            lines.append(departure)
        if arrival:
            lines.append(f"Прибытие: {arrival}")
        rows.append([InlineKeyboardButton(f"🚆 {index} · {route}", callback_data=
            _trip_callback_with_source("card", trip.context_id, kind, value.event.context_id, page))])
    rows += [[InlineKeyboardButton("⬅️ К событию", callback_data=_event_back_callback(value, kind, page, actor_key))],
             [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _selector_moment(day, clock) -> str | None:
    if not day:
        return None
    months = ("", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря")
    return f"{day.day} {months[day.month]}" + (f" · {clock:%H:%M}" if clock else "")


def _event_back_callback(value: EventActionContext, kind: str, page: int, actor_key: str) -> str:
    event = value.event
    return (f"view|afisha|{event.canonical_parent_id}|{page}" if kind == "a"
            else f"cal_view|{event.owner_scope if kind == 'c' else actor_key}|"
                 f"{event.canonical_parent_id if kind == 'c' else build_afisha_projection_id(event.canonical_parent_id, actor_key)}|{page}")


async def contextual_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await remember_current_chat(update)
    parts = str(query.data).split(":")
    if len(parts) != 6:
        return SECTION
    _, _, action, kind, parent_id, page_raw = parts
    callback_parent_id = parent_id
    parent_type = "afisha" if kind in {"a", "p"} else "calendar"
    try:
        page = int(page_raw)
    except ValueError:
        page = 0
    actor_key = get_wishlist_owner_by_user(update)
    data = storage.load()
    if kind == "p":
        projection = next((row for row in data.get("calendars", {}).get(actor_key, [])
                           if str(row.get("id") or "") == parent_id and row.get("source") == "afisha"), None)
        parent_id = str(projection.get("source_id") or "") if projection else ""
    value = resolve_event_action_context(data, actor_key=actor_key, parent_type=parent_type,
        parent_id=parent_id, now=datetime.now(), timezone=BOT_TIMEZONE)
    logger.info("Context event action action=%s outcome=%s candidate_count=%s", action,
                "resolved" if value else "stale", value.document_count if value else 0)
    back = (_back("calendar", build_afisha_projection_id(parent_id, actor_key), page, actor_key)
            if kind == "p" else _back(parent_type, parent_id, page, actor_key))
    if value is None:
        await _safe_edit_message(query, "Событие больше недоступно.", reply_markup=back)
        return SECTION
    if action == "docs":
        if not value.document_count:
            await _safe_edit_message(query, "К этому событию больше нет сохранённых документов.", reply_markup=back)
            return SECTION
        # This is the established canonical attachment list/detail/send flow.
        return await show_documents(update, context, parent_type, parent_id,
                                    f"ctx:event:overview:{kind}:{callback_parent_id}:{page}")
    if action == "trip":
        if len(value.trips) > 1:
            text, keyboard = _trip_selector(value, kind, page, actor_key)
            await _safe_edit_message(query, text, reply_markup=keyboard)
            return SECTION
        if value.trips:
            trip = value.trips[0]
            trip_value = resolve_trip_action_context(data, actor_key=actor_key,
                trip_context_id=trip.context_id, now=datetime.now(), timezone=BOT_TIMEZONE)
            text = render_trip_card(trip_value)
            await _safe_edit_message(query, text, reply_markup=_trip_keyboard(
                trip_value, back_callback=back.inline_keyboard[0][0].callback_data))
            return SECTION
        text = "Для этого события больше нет однозначно связанной поездки."
    elif action == "overview":
        text = render_overview(value)
    elif action == "weather":
        text = await _weather_text(value)
    else:
        text = "Действие больше недоступно."
    await _safe_edit_message(query, text, reply_markup=back)
    return SECTION


async def contextual_trip_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Rebuild a selector from its opaque canonical event identity."""
    query = update.callback_query
    await query.answer()
    await remember_current_chat(update)
    parts = str(query.data).split(":")
    if len(parts) != 5:
        return SECTION
    _, _, kind, event_id, page_raw = parts
    try:
        page = int(page_raw)
    except ValueError:
        page = 0
    actor_key = get_wishlist_owner_by_user(update)
    value = resolve_event_action_context_id(storage.load(), actor_key=actor_key,
        event_context_id=event_id, now=datetime.now(), timezone=BOT_TIMEZONE)
    if value is None:
        await _safe_edit_message(query, "Событие больше недоступно.",
                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]))
        return SECTION
    text, keyboard = _trip_selector(value, kind, page, actor_key)
    await _safe_edit_message(query, text, reply_markup=keyboard)
    return SECTION


async def contextual_trip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Re-authorize every trip action against a fresh Context Engine bundle."""
    query = update.callback_query
    await query.answer()
    await remember_current_chat(update)
    parts = str(query.data).split(":")
    if len(parts) not in {4, 7}:
        return SECTION
    _, _, action, trip_id = parts[:4]
    source = None
    if len(parts) == 7:
        kind, event_id, page_raw = parts[4:]
        try:
            source = (kind, event_id, int(page_raw))
        except ValueError:
            return SECTION
    actor_key = get_wishlist_owner_by_user(update)
    value = resolve_trip_action_context(storage.load(), actor_key=actor_key,
        trip_context_id=trip_id, now=datetime.now(), timezone=BOT_TIMEZONE)
    if value is None:
        logger.info("contextual trip action action=%s outcome=stale document_count=0 linked_event_count=0", action)
        await _safe_edit_message(query, "Эта поездка больше недоступна.",
                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]))
        return SECTION
    card_callback = (_trip_callback_with_source("card", trip_id, *source)
                     if source else trip_callback("card", trip_id))
    back = _trip_keyboard(value, back_callback=card_callback, source=source)
    outcome = "found"
    if action == "card":
        text = render_trip_card(value)
        selector_back = _trips_callback(source[0], source[1], source[2]) if source else "menu:main"
        back = _trip_keyboard(value, back_callback=selector_back, source=source)
    elif action == "route":
        text = render_trip_route(value.trip)
    elif action == "overview":
        text = render_trip_overview(value)
    elif action == "weather":
        text, outcome = await _trip_weather_text(value)
    elif action == "docs":
        if not value.documents:
            text, outcome = "К этой поездке больше нет сохранённых документов.", "missing_data"
        else:
            parents = {(row.parent_type, row.parent_id) for row in value.documents}
            if len(parents) == 1:
                parent_type, parent_id = next(iter(parents))
                logger.info("contextual trip action action=docs outcome=found document_count=%s linked_event_count=%s",
                            len(value.documents), len(value.linked_events))
                return await show_documents(update, context, parent_type, parent_id,
                                            card_callback, read_only=True)
            text = "📎 Документы\n\n" + "\n".join(f"• {row.semantic_type}" for row in value.documents)
    else:
        text, outcome = "Действие больше недоступно.", "missing_data"
    logger.info("contextual trip action action=%s outcome=%s document_count=%s linked_event_count=%s",
                action, outcome, len(value.documents), len(value.linked_events))
    await _safe_edit_message(query, text, reply_markup=back)
    return SECTION


async def _trip_weather_text(value) -> tuple[str, str]:
    trip = value.trip
    location, day = trip_weather_target(trip)
    if not location or not day:
        return "Не хватает данных, чтобы определить погоду для этой поездки.", "missing_data"
    try:
        forecast = await _weather_provider.get_forecast(location, day, day)
    except WeatherHorizonUnavailable:
        return "Прогноз на эту дату ещё недоступен.", "missing_data"
    except WeatherError:
        return "Сейчас не получилось получить прогноз. Попробуй чуть позже.", "provider_error"
    return format_forecast(forecast, context_note="По датам сохранённой поездки.", include_advice=True), "found"


async def _weather_text(value: EventActionContext) -> str:
    trip, event = value.trip, value.event
    location = (trip.city_hint or trip.destination) if trip else (event.location_text or "Москва")
    day = trip.arrival_date if trip and trip.arrival_date else event.date
    try:
        forecast = await _weather_provider.get_forecast(location, day, day)
    except WeatherHorizonUnavailable:
        logger.info("Context event action action=weather outcome=horizon candidate_count=1")
        return "Прогноз на эту дату ещё недоступен."
    except WeatherError:
        logger.info("Context event action action=weather outcome=failed candidate_count=1")
        return "Сейчас не получилось получить прогноз. Попробуй чуть позже."
    logger.info("Context event action action=weather outcome=found candidate_count=1")
    return format_forecast(forecast, context_note="По датам сохранённого события.", include_advice=True)
