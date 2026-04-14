from __future__ import annotations

from typing import Any, Callable

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.states import ADDING_BACKLOG_DESCRIPTION, ADDING_BACKLOG_TITLE, SECTION
from bot.storage import make_id, normalize_backlog_item, storage
from bot.utils import ensure_access, remember_current_chat

_build_item_text: Callable[[str, dict[str, Any]], str] | None = None
_item_keyboard: Callable[..., Any] | None = None


def configure_backlog_handlers(
    *,
    build_item_text: Callable[[str, dict[str, Any]], str],
    item_keyboard: Callable[..., Any],
) -> None:
    global _build_item_text, _item_keyboard
    _build_item_text = build_item_text
    _item_keyboard = item_keyboard


async def add_backlog_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Название фичи не должно быть пустым. Попробуй ещё раз:")
        return ADDING_BACKLOG_TITLE
    context.user_data["backlog_title"] = title
    await update.message.reply_text("Теперь отправь описание. Если не нужно, напиши -")
    return ADDING_BACKLOG_DESCRIPTION


async def add_backlog_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    description = (update.message.text or "").strip()
    if description == "-":
        description = ""

    item = {
        "id": make_id(),
        "title": context.user_data.get("backlog_title", "Без названия"),
        "description": description,
        "status": "todo",
    }
    normalized_item = normalize_backlog_item(item)
    if normalized_item is None:
        await update.message.reply_text("Не удалось сохранить фичу.")
        return SECTION

    data = storage.load()
    data["backlog"].append(normalized_item)
    storage.save(data)

    context.user_data.pop("backlog_title", None)
    context.user_data["active_section"] = "backlog"

    if _build_item_text is None or _item_keyboard is None:
        raise RuntimeError("Backlog handlers are not configured")

    await update.message.reply_text(
        f"Фича добавлена в бэклог:\n\n{_build_item_text('backlog', normalized_item)}",
        reply_markup=_item_keyboard("backlog", normalized_item, page=0, status_filter="todo"),
    )
    return SECTION
