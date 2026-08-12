from __future__ import annotations

from datetime import datetime
import logging
import time
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from bot.services.event_attachments import (create_event_attachment, delete_event_attachment,
    get_attachments_for_event, get_event_attachment, resolve_attachment_parent, update_event_attachment_metadata)
from bot.services.event_attachment_display import attachment_detail_text, attachment_list_titles
from bot.services.ticket_enrichment import PolzaTicketEnricher, TicketEnrichmentError
from bot.config import AI_ATTACHMENT_MAX_BYTES, AI_PROPOSAL_TTL_SECONDS, BOT_TIMEZONE
from bot.states import (ADDING_EVENT_ATTACHMENT_FILE, EDITING_EVENT_ATTACHMENT_METADATA,
                        ENRICHING_EVENT_ATTACHMENT, CONFIRMING_TICKET_ENRICHMENT, CONFIRMING_NL_ATTACHMENT,
                        SELECTING_EVENT_ATTACHMENT_TRANSPORT,
                        SELECTING_EVENT_ATTACHMENT_TYPE, SECTION)
from bot.storage import storage
from bot.utils import ensure_access, get_user_name, remember_current_chat

_safe_edit_message: Callable[..., Awaitable[None]] | None = None
_ticket_enricher: PolzaTicketEnricher | None = None
_attachment_max_bytes = AI_ATTACHMENT_MAX_BYTES
logger = logging.getLogger(__name__)

TYPE_LABELS = {"transport_ticket": "🎟 Билет", "voucher": "🏨 Ваучер / проживание",
              "insurance": "🛡 Страховка", "reservation": "📅 Бронь", "other": "📄 Документ"}
TRANSPORT_LABELS = {"train": "на поезд", "plane": "на самолёт", "bus": "на автобус", "other": ""}


def extract_attachment_draft(message: Any) -> dict[str, str] | None:
    """Copy Telegram references only; this deliberately performs no download."""
    if getattr(message, "document", None):
        file = message.document
        return {"telegram_media_type": "document", "telegram_file_id": file.file_id,
                "telegram_file_unique_id": file.file_unique_id, "file_name": file.file_name or "",
                "mime_type": file.mime_type or ""}
    photos = getattr(message, "photo", None) or []
    if photos:
        file = photos[-1]
        return {"telegram_media_type": "photo", "telegram_file_id": file.file_id,
                "telegram_file_unique_id": file.file_unique_id, "file_name": "", "mime_type": "image/jpeg"}
    return None


def configure_event_attachment_handlers(*, safe_edit_message: Callable[..., Awaitable[None]],
                                        ticket_enricher: PolzaTicketEnricher | None = None,
                                        attachment_max_bytes: int = AI_ATTACHMENT_MAX_BYTES) -> None:
    global _safe_edit_message, _ticket_enricher, _attachment_max_bytes
    _safe_edit_message = safe_edit_message
    _ticket_enricher = ticket_enricher
    _attachment_max_bytes = attachment_max_bytes


def _edit():
    if _safe_edit_message is None:
        raise RuntimeError("Event attachment handlers are not configured")
    return _safe_edit_message


def _back(context: ContextTypes.DEFAULT_TYPE) -> str:
    return str(context.user_data.get("event_attachment_back") or "menu:main")


async def show_documents(update: Update, context: ContextTypes.DEFAULT_TYPE, parent_type: str, parent_id: str, back: str) -> int:
    data = storage.load()
    try:
        resolved_type, resolved_id, event = resolve_attachment_parent(data, parent_type, parent_id)
    except ValueError:
        await _edit()(update.callback_query, "Событие не найдено.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]))
        return SECTION
    context.user_data["event_attachment_parent"] = (resolved_type, resolved_id)
    context.user_data["event_attachment_back"] = back
    items = get_attachments_for_event(data, resolved_type, resolved_id)
    titles = attachment_list_titles(items)
    rows = [[InlineKeyboardButton(title, callback_data=f"att|detail|{item['id']}")] for item, title in zip(items, titles)]
    rows += [[InlineKeyboardButton("➕ Добавить", callback_data="att|add")],
             [InlineKeyboardButton("⬅️ Назад", callback_data=back)],
             [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]
    body = "\n".join(titles) if items else "Пока документов нет."
    await _edit()(update.callback_query, f"📎 Документы\n{event.get('title', 'Событие')}\n\n{body}", reply_markup=InlineKeyboardMarkup(rows))
    return SECTION


async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update): return ConversationHandler.END
    await remember_current_chat(update)
    message = update.message
    draft = extract_attachment_draft(message)
    if draft is None:
        await message.reply_text("Нужен PDF, документ или фото.")
        return ADDING_EVENT_ATTACHMENT_FILE
    context.user_data["event_attachment_draft"] = draft
    rows = [[InlineKeyboardButton(label, callback_data=f"att|type|{kind}")] for kind, label in TYPE_LABELS.items()]
    await message.reply_text("Что это за документ?", reply_markup=InlineKeyboardMarkup(rows))
    return SELECTING_EVENT_ATTACHMENT_TYPE


def _save(context: ContextTypes.DEFAULT_TYPE, semantic_type: str, transport_type: str | None = None,
          **metadata: Any) -> tuple[dict[str, Any], bool] | None:
    draft = context.user_data.get("event_attachment_draft") or {}
    if not draft.get("telegram_file_id"):
        return None
    data = storage.load()
    parent_type, parent_id = context.user_data["event_attachment_parent"]
    item, created = create_event_attachment(data, parent_type=parent_type, parent_event_id=parent_id,
        semantic_type=semantic_type, transport_type=transport_type,
        created_by=str(context.user_data.get("event_attachment_creator") or "unknown"), **metadata, **draft)
    storage.save(data); context.user_data.pop("event_attachment_draft", None)
    return item, created


async def show_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, attachment_id: str) -> int:
    item = get_event_attachment(storage.load(), attachment_id)
    if not item:
        await _edit()(update.callback_query, "Документ не найден.")
        return SECTION
    rows = [[InlineKeyboardButton("📤 Отправить файл", callback_data=f"att|send|{attachment_id}")]]
    if item.get("semantic_type") == "transport_ticket" and _ticket_enricher is not None:
        rows.append([InlineKeyboardButton("✨ Распознать данные", callback_data=f"att|recognize|{attachment_id}")])
    rows += [
            [InlineKeyboardButton("✏️ Изменить данные", callback_data=f"att|edit|{attachment_id}")],
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"att|delconfirm|{attachment_id}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="att|return")]]
    await _edit()(update.callback_query, attachment_detail_text(item), reply_markup=InlineKeyboardMarkup(rows)); return SECTION


def _metadata_prompt(field: str) -> str:
    return {"origin": "Укажи пункт отправления", "destination": "Укажи пункт назначения",
            "date": "Укажи дату в формате ГГГГ-ММ-ДД", "departure_time": "Укажи время в формате ЧЧ:ММ",
            "arrival_date": "Укажи дату прибытия в формате ГГГГ-ММ-ДД",
            "arrival_time": "Укажи время прибытия в формате ЧЧ:ММ",
            "person": "Для кого: Вова, Саша или оба"}[field] + ". Отправь «-», чтобы очистить/пропустить."


async def receive_attachment_metadata(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle one deterministic field for native enrichment or editing."""
    if not await ensure_access(update): return ConversationHandler.END
    await remember_current_chat(update)
    flow = context.user_data.get("event_attachment_metadata_flow") or {}
    fields = flow.get("fields") or []
    if not fields: return SECTION
    field = fields[0]; raw = (update.message.text or "").strip(); value = None if raw == "-" else raw
    if field == "person":
        value = {"вова": "vova", "саша": "sasha", "оба": "both", "обоим": "both"}.get(
            value.casefold() if value else "", value)
        if value not in {None, "vova", "sasha", "both"}:
            await update.message.reply_text("Выбери Вова, Саша, оба или «-»."); return EDITING_EVENT_ATTACHMENT_METADATA
    if field in {"date", "arrival_date"} and value:
        from datetime import datetime
        try: value = datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            await update.message.reply_text("Неверная дата. Используй ГГГГ-ММ-ДД или «-»."); return flow["state"]
    if field in {"departure_time", "arrival_time"} and value:
        from datetime import datetime
        try: value = datetime.strptime(value, "%H:%M").strftime("%H:%M")
        except ValueError:
            await update.message.reply_text("Неверное время. Используй ЧЧ:ММ или «-»."); return flow["state"]
    if flow["mode"] == "native":
        context.user_data.setdefault("event_attachment_enrichment", {})[field] = value
    elif flow["mode"] == "nl_create":
        from bot.services.nl_attachment_context import KEY
        operation = context.user_data.get(KEY)
        if operation is None or operation.operation_id != flow.get("operation_id"): return SECTION
        operation.metadata[field] = value
    else:
        data = storage.load()
        try: update_event_attachment_metadata(data, flow["attachment_id"], **{field: value})
        except ValueError:
            await update.message.reply_text("Не удалось изменить данные."); return SECTION
        storage.save(data)
    fields.pop(0)
    if fields:
        await update.message.reply_text(_metadata_prompt(fields[0])); return flow["state"]
    context.user_data.pop("event_attachment_metadata_flow", None)
    if flow["mode"] == "native":
        metadata = context.user_data.pop("event_attachment_enrichment", {})
        _save(context, "transport_ticket", flow["transport_type"], **metadata)
        await update.message.reply_text("Документ сохранён.")
    elif flow["mode"] == "nl_create":
        from bot.services.nl_attachment_context import KEY
        operation = context.user_data.get(KEY)
        if operation is not None:
            operation.stage = "confirm"
            from bot.handlers.nl_event_attachments import _confirm_text
            await update.message.reply_text(_confirm_text(operation), reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Прикрепить", callback_data=f"nla:c:{operation.operation_id}")],
                [InlineKeyboardButton("❌ Отменить", callback_data=f"nla:x:{operation.operation_id}")]]))
            return CONFIRMING_NL_ATTACHMENT
    else:
        await update.message.reply_text("Данные обновлены.")
    return SECTION


async def event_attachment_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update): return ConversationHandler.END
    await remember_current_chat(update); query = update.callback_query; await query.answer(); parts = query.data.split("|")
    action = parts[1]
    if action == "cal":
        _, _, owner, event_id, page = parts
        return await show_documents(update, context, "calendar", event_id, f"cal_view|{owner}|{event_id}|{page}")
    if action == "afi":
        _, _, event_id, page = parts
        return await show_documents(update, context, "afisha", event_id, f"view|afisha|{event_id}|{page}")
    if action == "return":
        _discard_ticket_proposal(context)
        context.user_data.pop("event_attachment_draft", None)
        parent_type, parent_id = context.user_data["event_attachment_parent"]
        return await show_documents(update, context, parent_type, parent_id, _back(context))
    if action == "add":
        context.user_data["event_attachment_creator"] = get_user_name(update)
        await _edit()(query, "Пришли PDF, документ или фото.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="att|return")]]))
        return ADDING_EVENT_ATTACHMENT_FILE
    if action == "type":
        semantic = parts[2]
        if semantic == "transport_ticket":
            rows = [[InlineKeyboardButton(label, callback_data=f"att|transport|{kind}")] for kind, label in
                    (("train", "🚆 Поезд"), ("plane", "✈️ Самолёт"), ("bus", "🚌 Автобус"), ("other", "Другое"))]
            await _edit()(query, "Выбери вид транспорта:", reply_markup=InlineKeyboardMarkup(rows)); return SELECTING_EVENT_ATTACHMENT_TRANSPORT
        _save(context, semantic); return await event_attachment_router_return(update, context)
    if action == "transport":
        context.user_data["event_attachment_transport_type"] = parts[2]
        rows = []
        if _ticket_enricher is not None:
            rows.append([InlineKeyboardButton("✨ Распознать из файла", callback_data="att|recognizenew")])
        rows += [[InlineKeyboardButton("✏️ Ввести вручную", callback_data="att|enrich")],
                [InlineKeyboardButton("Пропустить", callback_data="att|skipenrich")],
                [InlineKeyboardButton("❌ Отменить", callback_data="att|return")],
                [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]
        await _edit()(query, "Добавить данные билета?", reply_markup=InlineKeyboardMarkup(rows))
        return ENRICHING_EVENT_ATTACHMENT
    if action in {"recognizenew", "recognize"}:
        return await _recognize_ticket(update, context, None if action == "recognizenew" else parts[2])
    if action == "saveai":
        proposal = _ticket_proposal(context)
        changes = proposal.get("changes")
        if not isinstance(changes, dict):
            _discard_ticket_proposal(context); await query.message.reply_text("Предложение устарело. Запусти распознавание снова.")
            return SECTION
        if proposal.get("mode") == "create":
            if context.user_data.get("event_attachment_draft") != proposal.get("draft"):
                _discard_ticket_proposal(context); await query.message.reply_text("Предложение устарело. Запусти прикрепление снова.")
                return SECTION
            transport = proposal.get("transport_type") or "other"
            _discard_ticket_proposal(context)  # consume before the exactly-once create boundary
            saved = _save(context, "transport_ticket", transport, **changes)
            if not saved:
                await query.message.reply_text("Предложение устарело. Запусти прикрепление снова."); return SECTION
            await query.message.reply_text("Билет прикреплён.")
            return await event_attachment_router_return(update, context)
        attachment_id = proposal.get("attachment_id")
        if not attachment_id:
            _discard_ticket_proposal(context); await query.message.reply_text("Предложение устарело. Запусти распознавание снова.")
            return SECTION
        data = storage.load()
        try: update_event_attachment_metadata(data, attachment_id, **changes)
        except ValueError:
            _discard_ticket_proposal(context); await query.message.reply_text("Не удалось сохранить данные."); return SECTION
        storage.save(data); _discard_ticket_proposal(context)
        await query.message.reply_text("Данные билета сохранены.")
        return await show_detail(update, context, attachment_id)
    if action == "rejectai":
        proposal = _ticket_proposal(context); attachment_id = proposal.get("attachment_id")
        _discard_ticket_proposal(context)
        if attachment_id: return await show_detail(update, context, attachment_id)
        context.user_data.pop("event_attachment_draft", None)
        return SECTION
    if action == "editai":
        proposal = _ticket_proposal(context); attachment_id = proposal.get("attachment_id")
        fields = ["origin", "destination", "date", "departure_time", "arrival_date", "arrival_time"]
        if proposal.get("mode") == "create":
            context.user_data["event_attachment_enrichment"] = dict(proposal.get("changes") or {})
            context.user_data["event_attachment_metadata_flow"] = {"mode": "native", "fields": fields,
                "transport_type": proposal.get("transport_type") or "other", "state": ENRICHING_EVENT_ATTACHMENT}
            _discard_ticket_proposal(context)
            await _edit()(query, _metadata_prompt(fields[0])); return ENRICHING_EVENT_ATTACHMENT
        _discard_ticket_proposal(context)
        if not attachment_id: return SECTION
        context.user_data["event_attachment_metadata_flow"] = {"mode": "edit", "fields": fields,
            "attachment_id": attachment_id, "state": EDITING_EVENT_ATTACHMENT_METADATA}
        await _edit()(query, _metadata_prompt(fields[0])); return EDITING_EVENT_ATTACHMENT_METADATA
    if action == "skipenrich":
        _save(context, "transport_ticket", context.user_data.pop("event_attachment_transport_type", "other"))
        return await event_attachment_router_return(update, context)
    if action == "enrich":
        fields = ["origin", "destination", "date", "departure_time", "arrival_date", "arrival_time"]
        context.user_data["event_attachment_metadata_flow"] = {"mode": "native", "fields": fields,
            "transport_type": context.user_data.pop("event_attachment_transport_type", "other"), "state": ENRICHING_EVENT_ATTACHMENT}
        rows = [[InlineKeyboardButton("❌ Отменить", callback_data="att|return")],
                [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]
        await _edit()(query, _metadata_prompt(fields[0]), reply_markup=InlineKeyboardMarkup(rows))
        return ENRICHING_EVENT_ATTACHMENT
    if action == "edit":
        aid = parts[2]; item = get_event_attachment(storage.load(), aid)
        if not item: return SECTION
        fields = ["origin", "destination", "date", "departure_time", "arrival_date", "arrival_time", "person"] if item.get("semantic_type") == "transport_ticket" else []
        rows = [[InlineKeyboardButton(label, callback_data=f"att|editfield|{aid}|{field}")] for field, label in
                (("origin", "Откуда"), ("destination", "Куда"), ("date", "Дата"),
                 ("departure_time", "Время отправления"), ("arrival_date", "Дата прибытия"),
                 ("arrival_time", "Время прибытия"), ("person", "Для кого")) if field in fields]
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"att|detail|{aid}")])
        await _edit()(query, "Что изменить?", reply_markup=InlineKeyboardMarkup(rows)); return SECTION
    if action == "editfield":
        aid, field = parts[2], parts[3]
        context.user_data["event_attachment_metadata_flow"] = {"mode": "edit", "fields": [field],
            "attachment_id": aid, "state": EDITING_EVENT_ATTACHMENT_METADATA}
        rows = [[InlineKeyboardButton("❌ Отменить", callback_data=f"att|detail|{aid}")],
                [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]
        await _edit()(query, _metadata_prompt(field), reply_markup=InlineKeyboardMarkup(rows))
        return EDITING_EVENT_ATTACHMENT_METADATA
    if action == "detail": return await show_detail(update, context, parts[2])
    if action == "send":
        item = get_event_attachment(storage.load(), parts[2])
        if item:
            try:
                if item["telegram_media_type"] == "document": await context.bot.send_document(query.message.chat_id, item["telegram_file_id"])
                else: await context.bot.send_photo(query.message.chat_id, item["telegram_file_id"])
            except TelegramError:
                await query.message.reply_text("Не удалось отправить файл. Возможно, он больше недоступен в Telegram.")
        return await show_detail(update, context, parts[2])
    if action == "delconfirm":
        aid = parts[2]; rows = [[InlineKeyboardButton("✅ Удалить", callback_data=f"att|delete|{aid}")], [InlineKeyboardButton("❌ Отменить", callback_data=f"att|detail|{aid}")]]
        await _edit()(query, "🗑 Удалить документ?", reply_markup=InlineKeyboardMarkup(rows)); return SECTION
    if action == "delete":
        data = storage.load(); delete_event_attachment(data, parts[2]); storage.save(data)
        return await event_attachment_router_return(update, context)
    return SECTION


async def event_attachment_router_return(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    parent_type, parent_id = context.user_data["event_attachment_parent"]
    return await show_documents(update, context, parent_type, parent_id, _back(context))


def _discard_ticket_proposal(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("ticket_enrichment_proposal", None)
    context.user_data.pop("ticket_enrichment_in_progress", None)


def discard_ticket_enrichment(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Public escape-path hook used by main-menu handlers."""
    _discard_ticket_proposal(context)


def _ticket_proposal(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    proposal = context.user_data.get("ticket_enrichment_proposal") or {}
    if time.monotonic() - proposal.get("created_at", 0) > AI_PROPOSAL_TTL_SECONDS:
        _discard_ticket_proposal(context)
        return {}
    return proposal


def _media_kind(item: dict[str, Any]) -> tuple[str, str] | None:
    media, mime = item.get("telegram_media_type"), str(item.get("mime_type") or "").lower()
    if media == "photo": return "image", "image/jpeg"
    if media == "document" and mime == "application/pdf": return "pdf", mime
    if media == "document" and mime in {"image/jpeg", "image/png"}: return "image", mime
    return None


def _proposal_text(item: dict[str, Any], changes: dict[str, str]) -> str:
    # The centralized detail formatter is reused, so list/detail rendering stays identical after save.
    final = {**item, **changes}
    detail = attachment_detail_text(final)
    return f"✨ Я нашёл данные билета:\n\n{detail}"


async def enrich_ticket_reference(context: ContextTypes.DEFAULT_TYPE, reference: dict[str, Any],
                                  *, event_date: str | None) -> Any:
    """Analyze a Telegram reference without requiring or creating a canonical record."""
    if _ticket_enricher is None: raise TicketEnrichmentError("unavailable")
    media = _media_kind(reference)
    if media is None: raise TicketEnrichmentError("unsupported_media")
    telegram_file = await context.bot.get_file(reference["telegram_file_id"])
    declared_size = getattr(telegram_file, "file_size", None)
    if declared_size is not None and declared_size > _attachment_max_bytes:
        raise TicketEnrichmentError("oversize")
    content = bytes(await telegram_file.download_as_bytearray())
    try:
        if len(content) > _attachment_max_bytes: raise TicketEnrichmentError("oversize")
        return await _ticket_enricher.enrich(content, media[0], mime_type=media[1],
            local_date=datetime.now(ZoneInfo(BOT_TIMEZONE)).date(), timezone=BOT_TIMEZONE,
            event_date=event_date)
    finally:
        del content


def ticket_enrichment_available() -> bool:
    return _ticket_enricher is not None


async def _recognize_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE, attachment_id: str | None) -> int:
    query = update.callback_query
    if _ticket_enricher is None:
        await query.message.reply_text("Автоматическое распознавание сейчас недоступно. Данные можно добавить вручную.")
        return SECTION
    data = storage.load(); create_mode = attachment_id is None
    item = context.user_data.get("event_attachment_draft") if create_mode else get_event_attachment(data, attachment_id or "")
    if not item or (not create_mode and item.get("semantic_type") != "transport_ticket"):
        await query.message.reply_text("Билет не найден."); return SECTION
    if context.user_data.get("ticket_enrichment_in_progress") is not None:
        await query.message.reply_text("Билет уже анализируется. Дождись результата."); return SECTION
    if create_mode:
        fallback = InlineKeyboardMarkup([[InlineKeyboardButton("✏️ Ввести вручную", callback_data="att|enrich")],
            [InlineKeyboardButton("📎 Прикрепить без распознавания", callback_data="att|skipenrich")],
            [InlineKeyboardButton("❌ Отменить", callback_data="att|return")]])
    else:
        fallback = InlineKeyboardMarkup([[InlineKeyboardButton("✏️ Ввести вручную", callback_data=f"att|edit|{item['id']}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"att|detail|{item['id']}")]])
    operation = object(); context.user_data["ticket_enrichment_in_progress"] = operation
    progress = await query.message.reply_text("🔎 Анализирую билет…")
    async def finish(text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
        try: await progress.edit_text(text, reply_markup=reply_markup)
        except (TelegramError, AttributeError):
            try: await progress.delete()
            except (TelegramError, AttributeError): pass
            await query.message.reply_text(text, reply_markup=reply_markup)
    try:
        if create_mode: parent_type, parent_id = context.user_data["event_attachment_parent"]
        else: parent_type, parent_id = item["parent_type"], item["parent_event_id"]
        _, _, event = resolve_attachment_parent(data, parent_type, parent_id)
        result = await enrich_ticket_reference(context, item, event_date=event.get("date"))
    except TelegramError:
        logger.warning("ticket_enrichment_failed reason=telegram_download")
        await finish("Не удалось загрузить файл из Telegram. Данные можно добавить вручную.", fallback); return SECTION
    except TicketEnrichmentError:
        await finish("Не удалось распознать билет. Данные можно добавить вручную.", fallback); return SECTION
    finally:
        active = context.user_data.get("ticket_enrichment_in_progress") is operation
        if active: context.user_data.pop("ticket_enrichment_in_progress", None)
    if not active:
        try: await progress.delete()
        except (TelegramError, AttributeError): pass
        return SECTION
    if not result.useful:
        await finish("Не удалось уверенно распознать данные билета. Их можно добавить вручную.", fallback); return SECTION
    changes = {k: v for k, v in result.as_dict().items() if v is not None and (create_mode or not item.get(k))}
    if not changes:
        await finish("Все распознанные данные уже заполнены. При необходимости их можно изменить вручную.", fallback); return SECTION
    context.user_data["ticket_enrichment_proposal"] = {"mode": "create" if create_mode else "update_existing",
        "attachment_id": None if create_mode else item["id"], "changes": changes,
        "draft": dict(item) if create_mode else None,
        "transport_type": context.user_data.get("event_attachment_transport_type") if create_mode else None,
        "created_at": time.monotonic()}
    rows = [[InlineKeyboardButton("✅ Прикрепить" if create_mode else "✅ Сохранить", callback_data="att|saveai")],
            [InlineKeyboardButton("✏️ Изменить", callback_data="att|editai")],
            [InlineKeyboardButton("❌ Отменить" if create_mode else "❌ Не сохранять", callback_data="att|rejectai")]]
    await finish(_proposal_text({**item, "semantic_type": "transport_ticket",
        "transport_type": context.user_data.get("event_attachment_transport_type")}, changes), InlineKeyboardMarkup(rows))
    return CONFIRMING_TICKET_ENRICHMENT
