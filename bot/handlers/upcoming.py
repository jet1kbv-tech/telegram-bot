"""Native, read-only entry point for the existing UpcomingBrief projection."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import BOT_TIMEZONE
from bot.services.context_engine import build_context_bundle
from bot.services.nl_dates import zoned_now
from bot.services.upcoming_brief import build_upcoming_brief, render_upcoming_brief
from bot.services.important_dates import is_visible, occurrence_in_range
from bot.states import SECTION
from bot.storage import storage
from bot.utils import ensure_access, get_wishlist_owner_by_user

_safe_edit_message: Callable[..., Awaitable[None]] | None = None
RANGE_DAYS = {"today": 1, "7": 7, "30": 30}


def configure_upcoming_handlers(*, safe_edit_message: Callable[..., Awaitable[None]]) -> None:
    global _safe_edit_message
    _safe_edit_message = safe_edit_message


def upcoming_range(kind: str, now: datetime, timezone: str = BOT_TIMEZONE) -> tuple[date, date]:
    """Return an inclusive range anchored to the actor's local calendar day."""
    today = zoned_now(timezone, now).date()
    days = RANGE_DAYS.get(kind, RANGE_DAYS["7"])
    return today, today + timedelta(days=days - 1)


def upcoming_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Сегодня", callback_data="upcoming:today"),
         InlineKeyboardButton("7 дней", callback_data="upcoming:7")],
        [InlineKeyboardButton("30 дней", callback_data="upcoming:30")],
        [InlineKeyboardButton("📅 Календарь", callback_data="calendar_menu"),
         InlineKeyboardButton("🗓 Афиша", callback_data="menu|afisha")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])


async def upcoming_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    if _safe_edit_message is None:
        raise RuntimeError("Upcoming handlers are not configured.")

    query = update.callback_query
    await query.answer()
    kind = query.data.partition(":")[2]
    now = zoned_now(BOT_TIMEZONE)
    date_from, date_to = upcoming_range(kind, now)
    actor_key = get_wishlist_owner_by_user(update)
    snapshot = storage.load()
    bundle = build_context_bundle(snapshot, actor_key, now, BOT_TIMEZONE, include_past=True)
    birthdays = []
    for item in snapshot.get("important_dates", []):
        occurrence = occurrence_in_range(item, date_from, date_to) if is_visible(item, actor_key) else None
        if occurrence is not None:
            birthdays.append((item, occurrence))
    birthdays.sort(key=lambda pair: (pair[1], pair[0]["id"]))
    brief = build_upcoming_brief(bundle, actor_key=actor_key, date_from=date_from, date_to=date_to,
                                 important_dates=tuple(birthdays))
    await _safe_edit_message(
        query,
        render_upcoming_brief(brief, today=date_from),
        reply_markup=upcoming_keyboard(),
    )
    return SECTION
