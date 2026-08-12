from __future__ import annotations

import logging
import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import BOT_TIMEZONE
from bot.services.event_attachment_display import attachment_list_title, attachment_list_titles
from bot.services.event_attachment_query import query_event_attachments
from bot.services.event_attachments import delete_event_attachment, get_event_attachment, update_event_attachment_metadata
from bot.services.nl_attachment_mutation_context import clear_pending_mutation, create_pending_mutation, get_pending_mutation
from bot.services.nl_dates import DateExpressionError, resolve_date_expression, resolve_time_expression, zoned_now
from bot.services.nl_entity_resolution import resolve_attachment_events, upcoming_attachment_events
from bot.states import SELECTING_NL_ATTACHMENT_QUERY
from bot.storage import storage
from bot.utils import ensure_access, get_allowed_profile, get_username

logger = logging.getLogger(__name__)
QUERY_FIELDS = ("semantic_type", "transport_type", "origin", "destination", "date", "person", "direction")
CHANGE_FIELDS = ("origin", "destination", "date", "departure_time", "arrival_date", "arrival_time", "person")
FIELD_LABELS = {"origin": "Откуда", "destination": "Куда", "date": "Дата отправления",
                "departure_time": "Отправление", "arrival_date": "Дата прибытия",
                "arrival_time": "Прибытие", "person": "Пассажир"}


def _owner(update: Update) -> str:
    return str((get_allowed_profile(update) or {}).get("wishlist_owner") or "")


def _person(value: Any, owner: str) -> str | None:
    return {"current_user": owner, "other_user": "sasha" if owner == "vova" else "vova", "both": "both"}.get(value)


def _confirmation(operation, item: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    title = attachment_list_title(item)
    if operation.intent == "delete_event_attachment":
        text = f"🗑 Удалить документ?\n\n{title}\n\nФайл исчезнет из документов события."
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Удалить", callback_data=f"nlam:c:{operation.operation_id}")],
                                       [InlineKeyboardButton("❌ Отменить", callback_data=f"nlam:x:{operation.operation_id}")]])
        return text, markup
    lines = ["✏️ Изменить данные билета?", "", title, ""]
    for field, new in operation.changes.items():
        lines += [f"{FIELD_LABELS[field]}:", f"{item.get(field) or '—'} → {new}"]
    return "\n".join(lines), InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Сохранить", callback_data=f"nlam:c:{operation.operation_id}")],
        [InlineKeyboardButton("❌ Отменить", callback_data=f"nlam:x:{operation.operation_id}")],
    ])


async def _resolve(message: Any, operation, owner: str, user_data: dict[str, Any]) -> int:
    data = storage.load()
    parent = (operation.parent_type, operation.parent_id) if operation.parent_type else None
    result = query_event_attachments(data, owner=owner, canonical_parent=parent,
        **{key: (_person(operation.query[key], owner) if key == "person" else operation.query.get(key)) for key in QUERY_FIELDS}, limit=10)
    logger.info("NL attachment mutation intent=%s candidate_count=%s outcome=%s", operation.intent, result.total_count, result.outcome)
    operation.candidates = [candidate.attachment_id for candidate in result.candidates]
    if not operation.candidates:
        clear_pending_mutation(user_data)
        await message.reply_text("Не нашёл подходящий документ.")
        return SELECTING_NL_ATTACHMENT_QUERY
    if len(operation.candidates) > 1:
        titles = attachment_list_titles([candidate.attachment for candidate in result.candidates])
        rows = [[InlineKeyboardButton(title[:60], callback_data=f"nlam:a:{operation.operation_id}:{index}")]
                for index, title in enumerate(titles)]
        rows.append([InlineKeyboardButton("❌ Отменить", callback_data=f"nlam:x:{operation.operation_id}")])
        await message.reply_text("Нашёл несколько документов. Какой выбрать?", reply_markup=InlineKeyboardMarkup(rows))
        return SELECTING_NL_ATTACHMENT_QUERY
    operation.selected_id = operation.candidates[0]
    item = result.candidates[0].attachment
    if operation.intent == "update_event_attachment" and all(item.get(k) == v for k, v in operation.changes.items()):
        await message.reply_text("Это значение уже установлено.")
        return SELECTING_NL_ATTACHMENT_QUERY
    text, markup = _confirmation(operation, item)
    await message.reply_text(text, reply_markup=markup)
    return SELECTING_NL_ATTACHMENT_QUERY


async def begin_attachment_mutation(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                    intent: str, arguments: dict[str, Any], response: Any) -> int:
    now, owner = zoned_now(BOT_TIMEZONE), _owner(update)
    query = {key: arguments.get(key) for key in QUERY_FIELDS}
    try:
        if query.get("date"):
            query["date"] = resolve_date_expression(query["date"], now=now, timezone=BOT_TIMEZONE)
        changes = {field: arguments.get(f"new_{field}") for field in CHANGE_FIELDS if arguments.get(f"new_{field}") is not None}
        for field in ("date", "arrival_date"):
            if field in changes: changes[field] = resolve_date_expression(changes[field], now=now, timezone=BOT_TIMEZONE)
        for field in ("departure_time", "arrival_time"):
            if field in changes:
                if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(changes[field])) is None:
                    raise DateExpressionError("invalid_time")
                changes[field] = resolve_time_expression(changes[field])
        if "person" in changes: changes["person"] = _person(changes["person"], owner)
    except DateExpressionError:
        await response.reply_text("Не удалось распознать дату или время. Укажи корректное значение.")
        return SELECTING_NL_ATTACHMENT_QUERY
    if intent == "update_event_attachment" and not changes:
        await response.reply_text("Что именно нужно изменить? Сформулируй команду ещё раз.")
        return SELECTING_NL_ATTACHMENT_QUERY
    operation = create_pending_mutation(context.user_data, actor_key=get_username(update), intent=intent,
                                        now=now, query=query, changes=changes)
    target = arguments.get("target")
    if target:
        events = resolve_attachment_events(storage.load(), target, owner=owner, now=now, timezone=BOT_TIMEZONE)
        if len(events) == 1:
            operation.parent_type, operation.parent_id = events[0].bucket, events[0].item_id
        else:
            if not events: events = upcoming_attachment_events(storage.load(), owner=owner, now=now, timezone=BOT_TIMEZONE, limit=8)
            if not events:
                clear_pending_mutation(context.user_data); await response.reply_text("Не нашёл доступное событие."); return SELECTING_NL_ATTACHMENT_QUERY
            operation.candidates = [f"{event.bucket}:{event.item_id}" for event in events]
            rows = [[InlineKeyboardButton(str(event.item.get("title") or "Без названия")[:60], callback_data=f"nlam:e:{operation.operation_id}:{i}")]
                    for i, event in enumerate(events)]
            await response.reply_text("В каком событии искать документ?", reply_markup=InlineKeyboardMarkup(rows))
            return SELECTING_NL_ATTACHMENT_QUERY
    return await _resolve(response, operation, owner, context.user_data)


async def attachment_mutation_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update): return ConversationHandler.END
    callback = update.callback_query; await callback.answer()
    parts = (callback.data or "").split(":")
    operation = get_pending_mutation(context.user_data, actor_key=get_username(update), now=zoned_now(BOT_TIMEZONE),
                                     operation_id=parts[2] if len(parts) > 2 else "")
    if operation is None:
        await callback.edit_message_text("Эта операция уже завершена или устарела."); return SELECTING_NL_ATTACHMENT_QUERY
    action = parts[1]
    if action == "x":
        clear_pending_mutation(context.user_data); await callback.edit_message_text("Действие отменено. Ничего не изменилось."); return SELECTING_NL_ATTACHMENT_QUERY
    if action == "e":
        try: parent_type, parent_id = operation.candidates[int(parts[3])].split(":", 1)
        except (IndexError, ValueError): return SELECTING_NL_ATTACHMENT_QUERY
        operation.parent_type, operation.parent_id = parent_type, parent_id
        return await _resolve(callback.message, operation, _owner(update), context.user_data)
    if action == "a":
        try: operation.selected_id = operation.candidates[int(parts[3])]
        except (IndexError, ValueError): return SELECTING_NL_ATTACHMENT_QUERY
        item = get_event_attachment(storage.load(), operation.selected_id)
        if item is None: clear_pending_mutation(context.user_data); await callback.edit_message_text("Документ больше недоступен."); return SELECTING_NL_ATTACHMENT_QUERY
        if operation.intent == "update_event_attachment" and all(item.get(k) == v for k, v in operation.changes.items()):
            clear_pending_mutation(context.user_data); await callback.edit_message_text("Это значение уже установлено."); return SELECTING_NL_ATTACHMENT_QUERY
        text, markup = _confirmation(operation, item); await callback.edit_message_text(text, reply_markup=markup); return SELECTING_NL_ATTACHMENT_QUERY
    if action == "c" and operation.selected_id:
        attachment_id = operation.selected_id
        def mutate(data):
            # Recheck visibility and identity immediately before the atomic write.
            visible = query_event_attachments(data, owner=_owner(update), limit=25)
            if not any(match.attachment_id == attachment_id for match in visible.candidates): return "missing"
            if operation.intent == "delete_event_attachment": return "ok" if delete_event_attachment(data, attachment_id) else "missing"
            item = get_event_attachment(data, attachment_id)
            if item and all(item.get(k) == v for k, v in operation.changes.items()): return "same"
            update_event_attachment_metadata(data, attachment_id, **operation.changes); return "ok"
        try: outcome, _ = storage.update(mutate)
        except ValueError: outcome = "failed"
        clear_pending_mutation(context.user_data)
        logger.info("NL attachment mutation intent=%s outcome=%s", operation.intent, outcome)
        texts = {"ok": "Документ удалён." if operation.intent == "delete_event_attachment" else "Данные документа обновлены.",
                 "same": "Это значение уже установлено.", "missing": "Документ больше недоступен.", "failed": "Не удалось изменить документ."}
        await callback.edit_message_text(texts[outcome]); return SELECTING_NL_ATTACHMENT_QUERY
    return SELECTING_NL_ATTACHMENT_QUERY
