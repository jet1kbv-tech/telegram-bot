from __future__ import annotations

from typing import Any, Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.states import ADDING_WISHLIST_COMMENT, ADDING_WISHLIST_LINK, ADDING_WISHLIST_TITLE, SECTION
from bot.storage import make_id, storage
from bot.utils import ensure_access, get_wishlist_owner_by_user, remember_current_chat

_build_item_text: Callable[[str, dict[str, Any]], str] | None = None
_item_keyboard: Callable[..., Any] | None = None
_notify_other_user_about_wishlist_item: Callable[[ContextTypes.DEFAULT_TYPE, Update, dict[str, Any]], Awaitable[None]] | None = None


def configure_wishlist_handlers(
    *,
    build_item_text: Callable[[str, dict[str, Any]], str],
    item_keyboard: Callable[..., Any],
    notify_other_user_about_wishlist_item: Callable[[ContextTypes.DEFAULT_TYPE, Update, dict[str, Any]], Awaitable[None]],
) -> None:
    global _build_item_text, _item_keyboard, _notify_other_user_about_wishlist_item
    _build_item_text = build_item_text
    _item_keyboard = item_keyboard
    _notify_other_user_about_wishlist_item = notify_other_user_about_wishlist_item


async def add_wishlist_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Название не должно быть пустым. Попробуй ещё раз:")
        return ADDING_WISHLIST_TITLE
    context.user_data["wishlist_title"] = title
    await update.message.reply_text("Теперь отправь ссылку. Если ссылки нет, напиши -")
    return ADDING_WISHLIST_LINK


async def add_wishlist_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    link = (update.message.text or "").strip()
    if link == "-":
        link = ""
    context.user_data["wishlist_link"] = link
    await update.message.reply_text("Теперь отправь комментарий. Если не нужен, напиши -")
    return ADDING_WISHLIST_COMMENT


async def add_wishlist_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if _build_item_text is None or _item_keyboard is None or _notify_other_user_about_wishlist_item is None:
        raise RuntimeError("Wishlist handlers are not configured")

    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    comment = (update.message.text or "").strip()
    if comment == "-":
        comment = ""

    item = {
        "id": make_id(),
        "title": context.user_data.get("wishlist_title", "Без названия"),
        "link": context.user_data.get("wishlist_link", ""),
        "comment": comment,
        "status": "active",
        "owner": get_wishlist_owner_by_user(update),
        "reserved_by": "",
    }
    data = storage.load()
    data["wishlist"].append(item)
    storage.save(data)

    context.user_data.pop("wishlist_title", None)
    context.user_data.pop("wishlist_link", None)
    context.user_data["active_section"] = "wishlist"

    await update.message.reply_text(
        f"Пункт wishlist сохранён:\n\n{_build_item_text('wishlist', item)}",
        reply_markup=_item_keyboard("wishlist", item, page=0, owner=item["owner"]),
    )
    await _notify_other_user_about_wishlist_item(context, update, item)
    return SECTION
