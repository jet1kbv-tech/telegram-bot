from __future__ import annotations

import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import BOT_TIMEZONE
from bot.services.event_attachment_display import attachment_detail_text, attachment_list_titles
from bot.services.event_attachment_query import query_event_attachments
from bot.services.event_attachments import get_event_attachment, resolve_attachment_parent
from bot.services.nl_attachment_query_context import clear_pending_query, create_pending_query, get_pending_query
from bot.services.nl_dates import zoned_now
from bot.services.nl_entity_resolution import resolve_attachment_events, upcoming_attachment_events
from bot.states import SELECTING_NL_ATTACHMENT_QUERY
from bot.storage import storage
from bot.utils import ensure_access, get_allowed_profile, get_username

logger = logging.getLogger(__name__)


def _idle(context: ContextTypes.DEFAULT_TYPE) -> int:
    from bot.states import MENU, SECTION
    return SECTION if context.user_data.get("active_section") else MENU


def _owner(update: Update) -> str:
    return str((get_allowed_profile(update) or {}).get("wishlist_owner") or "")


def _event_keyboard(operation, candidates) -> InlineKeyboardMarkup:
    operation.candidates = [{"id": c.item_id, "bucket": c.bucket} for c in candidates]
    rows = [[InlineKeyboardButton(f"{c.item.get('title') or 'Без названия'} — {c.item.get('date') or 'без даты'}"[:60],
                                  callback_data=f"nlar:e:{operation.operation_id}:{i}")]
            for i, c in enumerate(candidates)]
    rows += [[InlineKeyboardButton("❌ Закрыть", callback_data=f"nlar:x:{operation.operation_id}")],
             [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]
    return InlineKeyboardMarkup(rows)


def _result_keyboard(operation, count: int) -> InlineKeyboardMarkup:
    rows = []
    for index in range(count):
        prefix = f"{index + 1}. " if count > 1 else ""
        rows.append([InlineKeyboardButton(f"📤 {prefix}Отправить файл", callback_data=f"nlar:s:{operation.operation_id}:{index}"),
                     InlineKeyboardButton(f"📎 {prefix}Открыть документ", callback_data=f"nlar:o:{operation.operation_id}:{index}")])
    rows.append([InlineKeyboardButton("❌ Закрыть", callback_data=f"nlar:x:{operation.operation_id}")])
    return InlineKeyboardMarkup(rows)


def _person(arguments: dict[str, Any], owner: str) -> str | None:
    return {"current_user": owner, "other_user": "sasha" if owner == "vova" else "vova",
            "both": "both"}.get(arguments.get("person"))


async def _show_results(message: Any, operation, *, owner: str) -> int:
    data = storage.load()
    parent = (operation.parent_type, operation.parent_id) if operation.parent_type and operation.parent_id else None
    args = operation.query
    result = query_event_attachments(data, owner=owner, canonical_parent=parent,
        semantic_type=args.get("semantic_type"), transport_type=args.get("transport_type"),
        origin=args.get("origin"), destination=args.get("destination"), date=args.get("date"),
        person=_person(args, owner), direction=args.get("direction"), limit=10)
    logger.info("NL attachment query intent=query_event_attachments candidate_count=%s outcome=%s",
                result.total_count, result.outcome)
    operation.candidates = [{"id": candidate.attachment_id} for candidate in result.candidates]
    if not result.candidates:
        await message.reply_text("Не нашёл подходящих документов.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Закрыть", callback_data=f"nlar:x:{operation.operation_id}")]]))
        return SELECTING_NL_ATTACHMENT_QUERY
    titles = attachment_list_titles([candidate.attachment for candidate in result.candidates])
    if len(result.candidates) == 1 and not args.get("return_all"):
        text = attachment_detail_text(result.candidates[0].attachment)
    else:
        text = "Найденные документы:\n\n" + "\n".join(f"{i + 1}. {title}" for i, title in enumerate(titles))
        if result.bounded: text += "\n\nПоказаны первые 10."
    await message.reply_text(text, reply_markup=_result_keyboard(operation, len(result.candidates)))
    return SELECTING_NL_ATTACHMENT_QUERY


async def begin_attachment_query(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 arguments: dict[str, Any], response: Any) -> int:
    now = zoned_now(BOT_TIMEZONE); owner = _owner(update)
    operation = create_pending_query(context.user_data, actor_key=get_username(update), now=now, query=arguments)
    target = arguments.get("target")
    if target:
        candidates = resolve_attachment_events(storage.load(), target, owner=owner, now=now, timezone=BOT_TIMEZONE)
        if len(candidates) == 1:
            operation.parent_type, operation.parent_id = candidates[0].bucket, candidates[0].item_id
            return await _show_results(response, operation, owner=owner)
        if not candidates:
            candidates = upcoming_attachment_events(storage.load(), owner=owner, now=now, timezone=BOT_TIMEZONE, limit=8)
            intro = "Не нашёл точного совпадения. Выбери событие, в котором искать документы:"
        else:
            intro = "Нашёл несколько событий. В каком искать документы?"
        if not candidates:
            clear_pending_query(context.user_data)
            await response.reply_text("Не нашёл доступных событий для поиска документов.")
            return _idle(context)
        await response.reply_text(intro, reply_markup=_event_keyboard(operation, candidates))
        return SELECTING_NL_ATTACHMENT_QUERY
    return await _show_results(response, operation, owner=owner)


async def attachment_query_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update): return ConversationHandler.END
    callback = update.callback_query; await callback.answer()
    parts = (callback.data or "").split(":")
    if len(parts) < 3: return _idle(context)
    operation = get_pending_query(context.user_data, actor_key=get_username(update),
                                  now=zoned_now(BOT_TIMEZONE), operation_id=parts[2])
    if operation is None:
        await callback.edit_message_text("Этот поиск уже завершён или устарел.")
        return _idle(context)
    action = parts[1]
    if action == "x":
        clear_pending_query(context.user_data); await callback.edit_message_text("Поиск документов закрыт."); return _idle(context)
    if action == "e":
        try: candidate = operation.candidates[int(parts[3])]
        except (IndexError, ValueError): return SELECTING_NL_ATTACHMENT_QUERY
        operation.parent_type, operation.parent_id = candidate["bucket"], candidate["id"]
        return await _show_results(callback.message, operation, owner=_owner(update))
    if action in {"s", "o"}:
        try: attachment_id = operation.candidates[int(parts[3])]["id"]
        except (IndexError, ValueError): return SELECTING_NL_ATTACHMENT_QUERY
        item = get_event_attachment(storage.load(), attachment_id)
        if item is None:
            await callback.message.reply_text("Документ больше недоступен."); return SELECTING_NL_ATTACHMENT_QUERY
        # Re-check current canonical visibility at delivery time.
        visible = query_event_attachments(storage.load(), owner=_owner(update),
            canonical_parent=(str(item.get("parent_type")), str(item.get("parent_event_id"))), limit=25)
        if not any(match.attachment_id == attachment_id for match in visible.candidates):
            await callback.message.reply_text("Документ больше недоступен."); return SELECTING_NL_ATTACHMENT_QUERY
        try:
            if item.get("telegram_media_type") == "photo":
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=item["telegram_file_id"])
            else:
                await context.bot.send_document(chat_id=update.effective_chat.id, document=item["telegram_file_id"])
        except Exception:
            logger.warning("NL attachment delivery failed outcome=telegram_error")
            await callback.message.reply_text("Telegram больше не может отправить этот файл. Попробуй открыть документы события.")
        return SELECTING_NL_ATTACHMENT_QUERY
    return SELECTING_NL_ATTACHMENT_QUERY
