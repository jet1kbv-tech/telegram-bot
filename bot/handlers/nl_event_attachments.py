from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import BOT_TIMEZONE
from bot.handlers.event_attachments import TYPE_LABELS, extract_attachment_draft
from bot.services.event_attachments import create_event_attachment, resolve_attachment_parent
from bot.services.nl_attachment_context import append_file, clear_pending, create_pending, get_pending
from bot.services.nl_dates import zoned_now
from bot.services.nl_entity_resolution import EntityCandidate, resolve_attachment_events, upcoming_attachment_events
from bot.states import (CONFIRMING_NL_ATTACHMENT, ENTERING_NL_ATTACHMENT_EVENT_TITLE,
                        SELECTING_NL_ATTACHMENT_EVENT, WAITING_FOR_NL_ATTACHMENTS)
from bot.storage import storage
from bot.utils import ensure_access, get_allowed_profile, get_user_name, get_username

logger = logging.getLogger(__name__)
TRANSPORT_LABELS = {"train": "поезд", "plane": "самолёт", "bus": "автобус", "other": "другое"}


def _idle(context: ContextTypes.DEFAULT_TYPE) -> int:
    from bot.states import MENU, SECTION
    return SECTION if context.user_data.get("active_section") else MENU


def _candidate_label(candidate: EntityCandidate | dict[str, Any]) -> str:
    item = candidate.item if isinstance(candidate, EntityCandidate) else candidate["item"]
    date = item.get("date") or "Дата не указана"
    return f"{item.get('title') or 'Без названия'} — {date}"


def _cancel_menu(operation_id: str) -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton("❌ Отменить", callback_data=f"nla:x:{operation_id}")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]


def _menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]])


def _candidate_rows(operation) -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(_candidate_label(candidate)[:60],
                                  callback_data=f"nla:e:{operation.operation_id}:{index}")]
            for index, candidate in enumerate(operation.candidates)]


async def _show_candidates(message: Any, operation, *, intro: str) -> int:
    rows = _candidate_rows(operation)
    rows.append([InlineKeyboardButton("🔎 Указать название", callback_data=f"nla:n:{operation.operation_id}")])
    rows += _cancel_menu(operation.operation_id)
    await message.reply_text(intro, reply_markup=InlineKeyboardMarkup(rows))
    return SELECTING_NL_ATTACHMENT_EVENT


async def _show_resolution_fallback(message: Any, operation, *, owner: str, now: datetime) -> int:
    """Keep the operation intact while the user deterministically selects its parent."""
    candidates = upcoming_attachment_events(
        storage.load(), owner=owner, now=now, timezone=BOT_TIMEZONE, limit=8,
    )
    operation.candidates = [
        {"id": candidate.item_id, "bucket": candidate.bucket, "item": candidate.item}
        for candidate in candidates
    ]
    if candidates:
        return await _show_candidates(
            message,
            operation,
            intro="Не нашёл точного совпадения. Выбери событие, к которому прикрепить документ:",
        )
    operation.stage = "enter_title"
    await message.reply_text(
        "Не нашёл точного совпадения и ближайших событий. Напиши точное название события.",
        reply_markup=InlineKeyboardMarkup(_cancel_menu(operation.operation_id)),
    )
    return ENTERING_NL_ATTACHMENT_EVENT_TITLE


def _classification(operation_id: str) -> InlineKeyboardMarkup:
    labels = [("transport_ticket", "🚆 Билет"), ("voucher", "🏨 Ваучер / проживание"),
              ("insurance", "🛡 Страховка"), ("reservation", "📅 Бронь"), ("other", "📄 Другое")]
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"nla:t:{operation_id}:{kind}")]
                                 for kind, label in labels] + _cancel_menu(operation_id))


def _waiting(operation_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data=f"nla:d:{operation_id}")]] + _cancel_menu(operation_id))


def _confirm_text(operation) -> str:
    metadata = operation.metadata
    lines = ["📎 Прикрепить документ?", "", f"Событие: {operation.event_title}",
             f"Тип: {TYPE_LABELS.get(metadata.get('semantic_type'), '📄 Документ')}"]
    if metadata.get("transport_type"):
        lines.append(f"Транспорт: {TRANSPORT_LABELS[metadata['transport_type']]}")
    if metadata.get("origin"): lines.append(f"Откуда: {metadata['origin']}")
    if metadata.get("destination"): lines.append(f"Куда: {metadata['destination']}")
    lines.append(f"Файлов: {len(operation.files)}")
    return "\n".join(lines)


async def _after_event(message: Any, operation) -> int:
    if operation.files:
        if not operation.metadata.get("semantic_type"):
            operation.stage = "classify"
            await message.reply_text("Что это за документ?", reply_markup=_classification(operation.operation_id))
            return SELECTING_NL_ATTACHMENT_EVENT
        operation.stage = "confirm"
        await message.reply_text(_confirm_text(operation), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Прикрепить", callback_data=f"nla:c:{operation.operation_id}")],
            [InlineKeyboardButton("❌ Отменить", callback_data=f"nla:x:{operation.operation_id}")]]))
        return CONFIRMING_NL_ATTACHMENT
    operation.stage = "collect"
    await message.reply_text(f"Хорошо. Пришли билет или несколько файлов — я прикреплю их к «{operation.event_title}».",
                             reply_markup=_waiting(operation.operation_id))
    return WAITING_FOR_NL_ATTACHMENTS


async def begin_intent_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                  arguments: dict[str, Any], response: Any) -> int:
    now = zoned_now(BOT_TIMEZONE); message = update.effective_message
    draft = extract_attachment_draft(message)
    operation = create_pending(context.user_data, actor_key=get_username(update), now=now,
        metadata={key: value for key, value in arguments.items() if key != "target" and value is not None},
        files=[draft] if draft else [])
    profile = get_allowed_profile(update) or {}; owner = str(profile.get("wishlist_owner") or "")
    candidates = resolve_attachment_events(storage.load(), arguments["target"], owner=owner, now=now, timezone=BOT_TIMEZONE)
    logger.info("NL attachment resolution candidate_count=%s file_count=%s", len(candidates), len(operation.files))
    if not candidates:
        return await _show_resolution_fallback(response, operation, owner=owner, now=now)
    operation.candidates = [{"id": c.item_id, "bucket": c.bucket, "item": c.item} for c in candidates]
    if len(candidates) > 1:
        return await _show_candidates(response, operation, intro="Нашёл несколько похожих событий. Какое выбрать?")
    operation.parent_type, operation.parent_id = candidates[0].bucket, candidates[0].item_id
    _, _, event = resolve_attachment_parent(storage.load(), operation.parent_type, operation.parent_id)
    operation.event_title = str(event.get("title") or "Событие")
    return await _after_event(response, operation)


async def orphan_attachment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update): return ConversationHandler.END
    message = update.effective_message; draft = extract_attachment_draft(message)
    if draft is None: return ConversationHandler.END
    now = zoned_now(BOT_TIMEZONE); profile = get_allowed_profile(update) or {}
    candidates = upcoming_attachment_events(storage.load(), owner=str(profile.get("wishlist_owner") or ""), now=now,
                                            timezone=BOT_TIMEZONE, limit=8)
    operation = create_pending(context.user_data, actor_key=get_username(update), now=now, metadata={}, files=[draft])
    operation.candidates = [{"id": c.item_id, "bucket": c.bucket, "item": c.item} for c in candidates]
    text = "К какому событию прикрепить этот документ?" if candidates else "Не нашёл ближайших событий. Укажи название."
    return await _show_candidates(message, operation, intro=text)


async def attachment_event_title_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Resolve a chooser fallback by exact title without invoking the provider."""
    if not await ensure_access(update): return ConversationHandler.END
    now = zoned_now(BOT_TIMEZONE)
    operation = get_pending(context.user_data, actor_key=get_username(update), now=now)
    if operation is None:
        await update.effective_message.reply_text("Эта операция уже устарела. Начни прикрепление заново.", reply_markup=_menu())
        return _idle(context)
    profile = get_allowed_profile(update) or {}
    candidates = resolve_attachment_events(storage.load(), update.effective_message.text or "",
        owner=str(profile.get("wishlist_owner") or ""), now=now, timezone=BOT_TIMEZONE)
    if not candidates:
        await update.effective_message.reply_text("Не нашёл такое событие. Укажи точное название ещё раз.",
                                                  reply_markup=InlineKeyboardMarkup(_cancel_menu(operation.operation_id)))
        return ENTERING_NL_ATTACHMENT_EVENT_TITLE
    operation.candidates = [{"id": candidate.item_id, "bucket": candidate.bucket, "item": candidate.item}
                            for candidate in candidates]
    if len(candidates) > 1:
        return await _show_candidates(update.effective_message, operation,
                                      intro="Нашёл несколько событий. Какое выбрать?")
    candidate = operation.candidates[0]
    operation.parent_type, operation.parent_id = candidate["bucket"], candidate["id"]
    _, _, event = resolve_attachment_parent(storage.load(), operation.parent_type, operation.parent_id)
    operation.event_title = str(event.get("title") or "Событие")
    return await _after_event(update.effective_message, operation)


async def collect_attachment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update): return ConversationHandler.END
    now = zoned_now(BOT_TIMEZONE); operation = get_pending(context.user_data, actor_key=get_username(update), now=now)
    if operation is None:
        await update.effective_message.reply_text("Эта операция уже устарела. Начни прикрепление заново.", reply_markup=_menu())
        return _idle(context)
    draft = extract_attachment_draft(update.effective_message)
    if draft is None:
        await update.effective_message.reply_text("Пришли PDF, документ или фото.", reply_markup=_waiting(operation.operation_id))
        return WAITING_FOR_NL_ATTACHMENTS
    append_file(operation, draft)
    logger.info("NL attachment collected file_count=%s", len(operation.files))
    await update.effective_message.reply_text(f"Получил файл. Всего: {len(operation.files)}.", reply_markup=_waiting(operation.operation_id))
    return WAITING_FOR_NL_ATTACHMENTS


async def nl_attachment_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update): return ConversationHandler.END
    query = update.callback_query; await query.answer(); parts = (query.data or "").split(":")
    action, operation_id = parts[1], parts[2]; now = zoned_now(BOT_TIMEZONE)
    operation = get_pending(context.user_data, actor_key=get_username(update), now=now, operation_id=operation_id)
    if operation is None:
        await query.edit_message_text("Эта операция уже завершена или устарела.", reply_markup=_menu())
        return _idle(context)
    if action == "x":
        clear_pending(context.user_data); await query.edit_message_text("Прикрепление отменено."); return _idle(context)
    if action == "n":
        operation.stage = "enter_title"
        await query.edit_message_text("Напиши точное название события.",
                                      reply_markup=InlineKeyboardMarkup(_cancel_menu(operation.operation_id)))
        return ENTERING_NL_ATTACHMENT_EVENT_TITLE
    if action == "e":
        try: candidate = operation.candidates[int(parts[3])]
        except (IndexError, ValueError): return SELECTING_NL_ATTACHMENT_EVENT
        operation.parent_type, operation.parent_id = candidate["bucket"], candidate["id"]
        _, _, event = resolve_attachment_parent(storage.load(), operation.parent_type, operation.parent_id)
        operation.event_title = str(event.get("title") or "Событие")
        return await _after_event(query.message, operation)
    if action == "d":
        if not operation.files:
            await query.edit_message_text("Я пока не получил ни одного файла.", reply_markup=_waiting(operation.operation_id))
            return WAITING_FOR_NL_ATTACHMENTS
        return await _after_event(query.message, operation)
    if action == "t":
        operation.metadata["semantic_type"] = parts[3]
        if parts[3] == "transport_ticket":
            rows = [[InlineKeyboardButton(label, callback_data=f"nla:r:{operation_id}:{kind}")]
                    for kind, label in (("train", "🚆 Поезд"), ("plane", "✈️ Самолёт"), ("bus", "🚌 Автобус"), ("other", "Другое"))]
            await query.edit_message_text("Выбери вид транспорта:", reply_markup=InlineKeyboardMarkup(rows + _cancel_menu(operation_id)))
            return SELECTING_NL_ATTACHMENT_EVENT
        return await _after_event(query.message, operation)
    if action == "r":
        operation.metadata["transport_type"] = parts[3]
        return await _after_event(query.message, operation)
    if action == "c":
        if operation.completed: return _idle(context)
        data = storage.load(); created = duplicates = 0
        # Resolve the canonical parent and validate every draft before mutating
        # the in-memory batch. Persistence still has exactly one save boundary.
        try:
            resolve_attachment_parent(data, operation.parent_type or "", operation.parent_id or "")
        except ValueError:
            clear_pending(context.user_data)
            await query.edit_message_text("Событие больше недоступно. Начни прикрепление заново.", reply_markup=_menu())
            return _idle(context)
        if not operation.files or any(
            draft.get("telegram_media_type") not in {"document", "photo"}
            or not draft.get("telegram_file_id") for draft in operation.files
        ):
            await query.edit_message_text("Не удалось проверить файлы. Начни прикрепление заново.", reply_markup=_menu())
            clear_pending(context.user_data)
            return _idle(context)
        profile = get_allowed_profile(update) or {}
        owner = str(profile.get("wishlist_owner") or "")
        person = {"current_user": owner, "other_user": "sasha" if owner == "vova" else "vova",
                  "both": "both"}.get(operation.metadata.get("person"))
        try:
            for draft in operation.files:
                _, was_created = create_event_attachment(data, parent_type=operation.parent_type or "",
                    parent_event_id=operation.parent_id or "", semantic_type=operation.metadata.get("semantic_type") or "other",
                    transport_type=operation.metadata.get("transport_type"), origin=operation.metadata.get("origin"),
                    destination=operation.metadata.get("destination"), person=person,
                    date_expression=operation.metadata.get("date_expression"), created_by=get_user_name(update), **draft)
                created += int(was_created); duplicates += int(not was_created)
        except ValueError:
            # ``data`` is a detached load result; without save, no partial batch
            # becomes persistent even if an earlier draft mutated this copy.
            clear_pending(context.user_data)
            await query.edit_message_text("Не удалось проверить файлы. Начни прикрепление заново.", reply_markup=_menu())
            return _idle(context)
        storage.save(data); operation.completed = True; clear_pending(context.user_data)
        logger.info("NL attachment saved file_count=%s duplicate_count=%s", created, duplicates)
        await query.edit_message_text(f"Готово. Прикреплено файлов: {created}.")
        return _idle(context)
    return SELECTING_NL_ATTACHMENT_EVENT
