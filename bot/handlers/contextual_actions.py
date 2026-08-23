"""Telegram callbacks for deterministic contextual event actions."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import BOT_TIMEZONE
from bot.handlers.event_attachments import show_documents
from bot.services.contextual_actions import (
    EventActionContext,
    render_overview,
    render_trip,
    resolve_event_action_context,
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


def contextual_action_rows(data: dict, *, actor_key: str, parent_type: str,
                           parent_id: str, page: int, now: datetime | None = None) -> list[list[InlineKeyboardButton]]:
    if actor_key not in {"vova", "sasha"}:
        return []
    value = resolve_event_action_context(data, actor_key=actor_key, parent_type=parent_type,
        parent_id=parent_id, now=now or datetime.now(), timezone=BOT_TIMEZONE)
    if value is None:
        return []
    labels = {"weather": "🌦 Погода", "docs": "📎 Документы", "trip": "🚆 Поездка", "overview": "🗓 Что известно"}
    buttons = [InlineKeyboardButton(labels[action], callback_data=_callback(action, parent_type, parent_id, page))
               for action in visible_actions(value)]
    return [buttons[index:index + 2] for index in range(0, len(buttons), 2)]


def _back(parent_type: str, parent_id: str, page: int, actor_key: str) -> InlineKeyboardMarkup:
    callback = (f"view|afisha|{parent_id}|{page}" if parent_type == "afisha"
                else f"cal_view|{actor_key}|{parent_id}|{page}")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ К событию", callback_data=callback)],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])


async def contextual_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await remember_current_chat(update)
    parts = str(query.data).split(":")
    if len(parts) != 6:
        return SECTION
    _, _, action, kind, parent_id, page_raw = parts
    parent_type = "afisha" if kind == "a" else "calendar"
    try:
        page = int(page_raw)
    except ValueError:
        page = 0
    actor_key = get_wishlist_owner_by_user(update)
    data = storage.load()
    value = resolve_event_action_context(data, actor_key=actor_key, parent_type=parent_type,
        parent_id=parent_id, now=datetime.now(), timezone=BOT_TIMEZONE)
    logger.info("Context event action action=%s outcome=%s candidate_count=%s", action,
                "resolved" if value else "stale", value.document_count if value else 0)
    back = _back(parent_type, parent_id, page, actor_key)
    if value is None:
        await _safe_edit_message(query, "Событие больше недоступно.", reply_markup=back)
        return SECTION
    if action == "docs":
        if not value.document_count:
            await _safe_edit_message(query, "К этому событию больше нет сохранённых документов.", reply_markup=back)
            return SECTION
        # This is the established canonical attachment list/detail/send flow.
        return await show_documents(update, context, parent_type, parent_id,
                                    f"ctx:event:overview:{kind}:{parent_id}:{page}")
    if action == "trip":
        text = render_trip(value.trip) if value.trip else "Для этого события больше нет однозначно связанной поездки."
    elif action == "overview":
        text = render_overview(value)
    elif action == "weather":
        text = await _weather_text(value)
    else:
        text = "Действие больше недоступно."
    await _safe_edit_message(query, text, reply_markup=back)
    return SECTION


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
