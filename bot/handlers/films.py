from __future__ import annotations

import random
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.states import (
    ADDING_FILM_COMMENT,
    ADDING_FILM_TITLE,
    SECTION,
)
from bot.storage import make_id, storage
from bot.utils import ensure_access, get_user_name, remember_current_chat

_safe_edit_message: Callable[..., Any] | None = None
_build_item_text: Callable[[str, dict[str, Any]], str] | None = None
_item_keyboard: Callable[..., Any] | None = None
_main_menu_keyboard: Callable[[], Any] | None = None

FILM_CONVERSATION_KEYS = (
    "film_title",
    "film_comment",
    "film_rating_item_id",
    "film_rating_page",
    "film_rating_status_filter",
    "pending_sasha_rating",
)


def clear_film_conversation_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Discard transient Films input while leaving navigation context intact."""
    for key in FILM_CONVERSATION_KEYS:
        context.user_data.pop(key, None)


def configure_films_handlers(
    *,
    safe_edit_message: Callable[..., Any],
    build_item_text: Callable[[str, dict[str, Any]], str],
    item_keyboard: Callable[..., Any],
    main_menu_keyboard: Callable[[], Any],
) -> None:
    global _safe_edit_message, _build_item_text, _item_keyboard, _main_menu_keyboard
    _safe_edit_message = safe_edit_message
    _build_item_text = build_item_text
    _item_keyboard = item_keyboard
    _main_menu_keyboard = main_menu_keyboard


def _ensure_configured() -> None:
    if _safe_edit_message is None or _build_item_text is None or _item_keyboard is None or _main_menu_keyboard is None:
        raise RuntimeError("Films handlers are not configured")


async def show_random_film(update: Update) -> int:
    _ensure_configured()
    query = update.callback_query
    data = storage.load()
    unwatched = [item for item in data.get("films", []) if item.get("status") == "want"]
    if not unwatched:
        await _safe_edit_message(
            query,
            "🎲 Непросмотренных фильмов пока нет.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить фильм", callback_data="add|films")],
                [InlineKeyboardButton("⬅️ Назад к фильмам", callback_data="menu|films")],
            ]),
        )
        return SECTION

    film = random.choice(unwatched)
    await _safe_edit_message(
        query,
        "🎲 Случайный выбор из непросмотренных:\n\n" + _build_item_text("films", film),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎲 Выбрать ещё", callback_data="random|films")],
            [InlineKeyboardButton("📋 Все непросмотренные", callback_data="list|films|want|0")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]),
    )
    return SECTION


async def add_film_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Название фильма не должно быть пустым. Попробуй ещё раз:")
        return ADDING_FILM_TITLE
    context.user_data["film_title"] = title
    await update.message.reply_text("Теперь отправь комментарий к фильму одним сообщением. Если не нужен, напиши -")
    return ADDING_FILM_COMMENT


async def add_film_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _ensure_configured()
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    comment = (update.message.text or "").strip()
    if comment == "-":
        comment = ""

    item = {
        "id": make_id(),
        "title": context.user_data.get("film_title", "Без названия"),
        "status": "want",
        "added_by": get_user_name(update),
        "comment": comment,
        "sasha_rating": None,
        "vova_rating": None,
        "legacy_rating": None,
        "external_id": "",
        "year": None,
        "genres": [],
        "description": "",
        "external_rating": None,
    }
    data = storage.load()
    data["films"].append(item)
    storage.save(data)

    clear_film_conversation_data(context)
    context.user_data["active_section"] = "films"

    await update.message.reply_text(
        f"Фильм сохранён:\n\n{_build_item_text('films', item)}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Добавить ещё", callback_data="add|films")],
            [InlineKeyboardButton("🎬 К фильмам", callback_data="menu|films")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]),
    )
    return SECTION
