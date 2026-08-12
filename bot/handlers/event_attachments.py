from __future__ import annotations

from typing import Any, Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from bot.services.event_attachments import (create_event_attachment, delete_event_attachment,
    get_attachments_for_event, get_event_attachment, resolve_attachment_parent, update_event_attachment_metadata)
from bot.services.event_attachment_display import attachment_detail_text, attachment_list_titles
from bot.states import (ADDING_EVENT_ATTACHMENT_FILE, EDITING_EVENT_ATTACHMENT_METADATA,
                        ENRICHING_EVENT_ATTACHMENT, SELECTING_EVENT_ATTACHMENT_TRANSPORT,
                        SELECTING_EVENT_ATTACHMENT_TYPE, SECTION)
from bot.storage import storage
from bot.utils import ensure_access, get_user_name, remember_current_chat

_safe_edit_message: Callable[..., Awaitable[None]] | None = None

TYPE_LABELS = {"transport_ticket": "🎟 Билет", "voucher": "🏨 Ваучер / проживание",
              "accommodation": "🏨 Ваучер / проживание",
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


def configure_event_attachment_handlers(*, safe_edit_message: Callable[..., Awaitable[None]]) -> None:
    global _safe_edit_message
    _safe_edit_message = safe_edit_message


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
    rows = [[InlineKeyboardButton("📤 Отправить файл", callback_data=f"att|send|{attachment_id}")],
            [InlineKeyboardButton("✏️ Изменить данные", callback_data=f"att|edit|{attachment_id}")],
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"att|delconfirm|{attachment_id}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="att|return")]]
    await _edit()(update.callback_query, attachment_detail_text(item), reply_markup=InlineKeyboardMarkup(rows)); return SECTION


def _metadata_prompt(field: str) -> str:
    return {"origin": "Укажи пункт отправления", "destination": "Укажи пункт назначения",
            "date": "Укажи дату в формате ГГГГ-ММ-ДД", "departure_time": "Укажи время в формате ЧЧ:ММ",
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
    if field == "date" and value:
        from datetime import datetime
        try: value = datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            await update.message.reply_text("Неверная дата. Используй ГГГГ-ММ-ДД или «-»."); return flow["state"]
    if field == "departure_time" and value:
        from datetime import datetime
        try: value = datetime.strptime(value, "%H:%M").strftime("%H:%M")
        except ValueError:
            await update.message.reply_text("Неверное время. Используй ЧЧ:ММ или «-»."); return flow["state"]
    if flow["mode"] == "native":
        context.user_data.setdefault("event_attachment_enrichment", {})[field] = value
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
        rows = [[InlineKeyboardButton("✏️ Добавить данные", callback_data="att|enrich")],
                [InlineKeyboardButton("Пропустить", callback_data="att|skipenrich")],
                [InlineKeyboardButton("❌ Отменить", callback_data="att|return")],
                [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]
        await _edit()(query, "Добавить данные билета?", reply_markup=InlineKeyboardMarkup(rows))
        return ENRICHING_EVENT_ATTACHMENT
    if action == "skipenrich":
        _save(context, "transport_ticket", context.user_data.pop("event_attachment_transport_type", "other"))
        return await event_attachment_router_return(update, context)
    if action == "enrich":
        fields = ["origin", "destination", "date", "departure_time"]
        context.user_data["event_attachment_metadata_flow"] = {"mode": "native", "fields": fields,
            "transport_type": context.user_data.pop("event_attachment_transport_type", "other"), "state": ENRICHING_EVENT_ATTACHMENT}
        rows = [[InlineKeyboardButton("❌ Отменить", callback_data="att|return")],
                [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]
        await _edit()(query, _metadata_prompt(fields[0]), reply_markup=InlineKeyboardMarkup(rows))
        return ENRICHING_EVENT_ATTACHMENT
    if action == "edit":
        aid = parts[2]; item = get_event_attachment(storage.load(), aid)
        if not item: return SECTION
        fields = ["origin", "destination", "date", "departure_time", "person"] if item.get("semantic_type") == "transport_ticket" else []
        rows = [[InlineKeyboardButton(label, callback_data=f"att|editfield|{aid}|{field}")] for field, label in
                (("origin", "Откуда"), ("destination", "Куда"), ("date", "Дата"),
                 ("departure_time", "Время отправления"), ("person", "Для кого")) if field in fields]
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
