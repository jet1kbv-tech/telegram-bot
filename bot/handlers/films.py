from __future__ import annotations

import random
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.states import (
    ADDING_FILM_COMMENT,
    ADDING_FILM_SASHA_RATING,
    ADDING_FILM_TITLE,
    ADDING_FILM_VOVA_RATING,
    MENU,
    SECTION,
)
from bot.storage import find_item, make_id, normalize_rating, storage
from bot.utils import ensure_access, get_user_name, remember_current_chat

_safe_edit_message: Callable[..., Any] | None = None
_build_item_text: Callable[[str, dict[str, Any]], str] | None = None
_item_keyboard: Callable[..., Any] | None = None
_main_menu_keyboard: Callable[[], Any] | None = None


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
            [InlineKeyboardButton("🏠 В меню", callback_data="main")],
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
    }
    data = storage.load()
    data["films"].append(item)
    storage.save(data)

    context.user_data.pop("film_title", None)
    context.user_data.pop("film_comment", None)
    context.user_data["active_section"] = "films"

    await update.message.reply_text(
        f"Фильм сохранён:\n\n{_build_item_text('films', item)}",
        reply_markup=_item_keyboard("films", item, page=0, status_filter="want"),
    )
    return SECTION


async def add_film_sasha_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    rating = normalize_rating((update.message.text or "").strip())
    if rating is None:
        await update.message.reply_text("Нужно отправить число от 1 до 10. Попробуй ещё раз:")
        return ADDING_FILM_SASHA_RATING

    context.user_data["pending_sasha_rating"] = rating
    await update.message.reply_text("Какую оценку Вова ставит фильму? Отправь число от 1 до 10.")
    return ADDING_FILM_VOVA_RATING


async def add_film_vova_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _ensure_configured()
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    rating = normalize_rating((update.message.text or "").strip())
    if rating is None:
        await update.message.reply_text("Нужно отправить число от 1 до 10. Попробуй ещё раз:")
        return ADDING_FILM_VOVA_RATING

    item_id = context.user_data.get("film_rating_item_id")
    page = int(context.user_data.get("film_rating_page", 0))
    status_filter = str(context.user_data.get("film_rating_status_filter") or "want")
    sasha_rating = context.user_data.get("pending_sasha_rating")

    data = storage.load()
    item = find_item(data.get("films", []), item_id)
    if not item:
        await update.message.reply_text("Не удалось сохранить оценки: фильм не найден.", reply_markup=_main_menu_keyboard())
        for key in ["film_rating_item_id", "film_rating_page", "film_rating_status_filter", "pending_sasha_rating"]:
            context.user_data.pop(key, None)
        return MENU

    item["sasha_rating"] = sasha_rating
    item["vova_rating"] = rating
    item["legacy_rating"] = None
    item["status"] = "watched"
    storage.save(data)

    for key in ["film_rating_item_id", "film_rating_page", "film_rating_status_filter", "pending_sasha_rating"]:
        context.user_data.pop(key, None)

    await update.message.reply_text(
        f"Фильм перенесён в просмотренные:\n\n{_build_item_text('films', item)}",
        reply_markup=_item_keyboard("films", item, page=page, status_filter="watched"),
    )
    return SECTION
