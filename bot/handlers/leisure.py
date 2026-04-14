from __future__ import annotations

from typing import Any, Callable

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.states import ADDING_LEISURE_COMMENT, ADDING_LEISURE_TITLE, SECTION
from bot.storage import make_id, storage
from bot.utils import ensure_access, remember_current_chat

_build_item_text: Callable[[str, dict[str, Any]], str] | None = None
_item_keyboard: Callable[..., Any] | None = None


def configure_leisure_handlers(
    *,
    build_item_text: Callable[[str, dict[str, Any]], str],
    item_keyboard: Callable[..., Any],
) -> None:
    global _build_item_text, _item_keyboard
    _build_item_text = build_item_text
    _item_keyboard = item_keyboard


async def add_leisure_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Идея не должна быть пустой. Попробуй ещё раз:")
        return ADDING_LEISURE_TITLE
    context.user_data["leisure_title"] = title
    await update.message.reply_text("Теперь отправь комментарий. Если не нужен, напиши -")
    return ADDING_LEISURE_COMMENT


async def add_leisure_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if _build_item_text is None or _item_keyboard is None:
        raise RuntimeError("Leisure handlers are not configured")

    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    comment = (update.message.text or "").strip()
    if comment == "-":
        comment = ""

    item = {
        "id": make_id(),
        "title": context.user_data.get("leisure_title", "Без названия"),
        "comment": comment,
        "status": "want",
    }
    data = storage.load()
    data["leisure"].append(item)
    storage.save(data)

    context.user_data.pop("leisure_title", None)
    context.user_data["active_section"] = "leisure"
    await update.message.reply_text(
        f"Идея для досуга сохранена:\n\n{_build_item_text('leisure', item)}",
        reply_markup=_item_keyboard("leisure", item, page=0),
    )
    return SECTION
