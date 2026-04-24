from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import PAGE_SIZE
from bot.handlers.afisha import apply_afisha_delete, apply_afisha_status_update
from bot.keyboards.tickets import (
    ticket_attachments_done_keyboard,
    ticket_card_keyboard,
    ticket_delete_confirm_keyboard,
    tickets_empty_list_keyboard,
    tickets_list_keyboard,
    tickets_menu_keyboard,
)
from bot.services.afisha_calendar_sync import project_afisha_to_calendars
from bot.states import (
    ADDING_TICKET_ATTACHMENTS,
    ADDING_TICKET_COMMENT,
    ADDING_TICKET_DATE,
    ADDING_TICKET_PLACE_ROUTE,
    ADDING_TICKET_TIME,
    ADDING_TICKET_TITLE,
    SECTION,
)
from bot.storage import (
    delete_item_by_id,
    find_item,
    make_id,
    normalize_event,
    normalize_tickets_root,
    sort_events,
    storage,
)
from bot.utils import ensure_access, remember_current_chat

_safe_edit_message: Callable[..., Awaitable[None]] | None = None


def configure_tickets_handlers(*, safe_edit_message: Callable[..., Awaitable[None]]) -> None:
    global _safe_edit_message
    _safe_edit_message = safe_edit_message


def _require_safe_edit_message() -> Callable[..., Awaitable[None]]:
    if _safe_edit_message is None:
        raise RuntimeError("Tickets handlers are not configured")
    return _safe_edit_message


def _tickets_root(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    normalize_tickets_root(data, data.get("tickets"))
    return data["tickets"]


def _parse_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _parse_time(value: str) -> bool:
    try:
        datetime.strptime(value, "%H:%M")
        return True
    except ValueError:
        return False


def _paginate(items: list[dict[str, Any]], page: int) -> tuple[list[dict[str, Any]], int, int]:
    if not items:
        return [], 0, 0
    total_pages = (len(items) + PAGE_SIZE - 1) // PAGE_SIZE
    current_page = max(0, min(page, total_pages - 1))
    start = current_page * PAGE_SIZE
    return items[start:start + PAGE_SIZE], current_page, total_pages


def _bucket_title(bucket: str) -> str:
    return "📋 Активные билеты" if bucket == "active" else "✅ Использованные билеты"


def _format_ticket_text(ticket: dict[str, Any]) -> str:
    lines = [
        f"🎟 {ticket.get('title', 'Без названия')}",
        f"Когда: {ticket.get('date', '')} {ticket.get('time', '')}".strip(),
    ]
    if ticket.get("place_route"):
        lines.append(f"Маршрут/место: {ticket['place_route']}")
    if ticket.get("comment"):
        lines.append(f"Комментарий: {ticket['comment']}")
    lines.append(f"Вложений: {len(ticket.get('attachments', []))}")
    if ticket.get("afisha_id"):
        lines.append("Связано с Афишей: да")
    return "\n".join(lines)


async def show_tickets_menu(update: Update) -> int:
    query = update.callback_query
    safe_edit_message = _require_safe_edit_message()
    await safe_edit_message(query, "🎟 Билеты\n\nВыбери действие:", reply_markup=tickets_menu_keyboard())
    return SECTION


async def show_tickets_list(update: Update, bucket: str, page: int) -> int:
    query = update.callback_query
    data = storage.load()
    tickets = _tickets_root(data).get(bucket, [])
    ordered = sorted(tickets, key=lambda item: f"{item.get('date', '')} {item.get('time', '')}")
    page_items, current_page, total_pages = _paginate(ordered, page)

    safe_edit_message = _require_safe_edit_message()
    if not ordered:
        await safe_edit_message(
            query,
            f"{_bucket_title(bucket)}\n\nПока пусто.",
            reply_markup=tickets_empty_list_keyboard(bucket),
        )
        return SECTION

    text = (
        f"{_bucket_title(bucket)}\n\n"
        f"Элементы {current_page * PAGE_SIZE + 1}–{min(len(ordered), current_page * PAGE_SIZE + len(page_items))} из {len(ordered)}."
    )
    await safe_edit_message(
        query,
        text,
        reply_markup=tickets_list_keyboard(page_items, bucket, current_page, total_pages),
    )
    return SECTION


async def show_ticket_card(update: Update, ticket_id: str, bucket: str, page: int) -> int:
    query = update.callback_query
    data = storage.load()
    ticket = find_item(_tickets_root(data).get(bucket, []), ticket_id)
    safe_edit_message = _require_safe_edit_message()
    if not ticket:
        await safe_edit_message(
            query,
            "Билет не найден.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К списку", callback_data=f"tickets:list:{bucket}:{page}")]]),
        )
        return SECTION

    await safe_edit_message(query, _format_ticket_text(ticket), reply_markup=ticket_card_keyboard(ticket_id, bucket, page))
    return SECTION


async def start_add_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    context.user_data["ticket_draft"] = {"attachments": []}
    safe_edit_message = _require_safe_edit_message()
    await safe_edit_message(query, "Отправь название билета:")
    return ADDING_TICKET_TITLE


async def add_ticket_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Название обязательно. Попробуй ещё раз:")
        return ADDING_TICKET_TITLE
    draft = context.user_data.setdefault("ticket_draft", {"attachments": []})
    draft["title"] = title
    await update.message.reply_text("Отправь дату в формате ГГГГ-ММ-ДД:")
    return ADDING_TICKET_DATE


async def add_ticket_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    date_raw = (update.message.text or "").strip()
    if not _parse_date(date_raw):
        await update.message.reply_text("Дата должна быть в формате ГГГГ-ММ-ДД. Попробуй ещё раз:")
        return ADDING_TICKET_DATE
    context.user_data.setdefault("ticket_draft", {"attachments": []})["date"] = date_raw
    await update.message.reply_text("Отправь время в формате ЧЧ:ММ:")
    return ADDING_TICKET_TIME


async def add_ticket_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    time_raw = (update.message.text or "").strip()
    if not _parse_time(time_raw):
        await update.message.reply_text("Время должно быть в формате ЧЧ:ММ. Попробуй ещё раз:")
        return ADDING_TICKET_TIME
    context.user_data.setdefault("ticket_draft", {"attachments": []})["time"] = time_raw
    await update.message.reply_text("Отправь место/маршрут или '-' чтобы пропустить:")
    return ADDING_TICKET_PLACE_ROUTE


async def add_ticket_place_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    value = (update.message.text or "").strip()
    context.user_data.setdefault("ticket_draft", {"attachments": []})["place_route"] = "" if value == "-" else value
    await update.message.reply_text("Добавь комментарий или '-' чтобы пропустить:")
    return ADDING_TICKET_COMMENT


async def add_ticket_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    value = (update.message.text or "").strip()
    context.user_data.setdefault("ticket_draft", {"attachments": []})["comment"] = "" if value == "-" else value
    await update.message.reply_text(
        "Теперь отправь один или несколько файлов билета (документы или фото). Когда закончишь, нажми кнопку:",
        reply_markup=ticket_attachments_done_keyboard(0),
    )
    return ADDING_TICKET_ATTACHMENTS


def _extract_attachment(update: Update) -> dict[str, Any] | None:
    message = update.message
    if not message:
        return None
    if message.document:
        return {
            "kind": "document",
            "file_id": message.document.file_id,
            "file_name": message.document.file_name or "",
            "mime_type": message.document.mime_type or "",
        }
    if message.photo:
        photo = message.photo[-1]
        return {
            "kind": "photo",
            "file_id": photo.file_id,
            "file_name": "",
            "mime_type": "image/jpeg",
        }
    return None


async def add_ticket_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    attachment = _extract_attachment(update)
    if attachment is None:
        await update.message.reply_text("Нужен документ или фото. Отправь файл билета или нажми 'Готово'.")
        return ADDING_TICKET_ATTACHMENTS

    draft = context.user_data.setdefault("ticket_draft", {"attachments": []})
    attachments = draft.setdefault("attachments", [])
    if len(attachments) >= 20:
        await update.message.reply_text("Достигнут лимит 20 вложений на билет.")
        return ADDING_TICKET_ATTACHMENTS
    attachments.append(attachment)
    await update.message.reply_text(
        f"Добавлено вложений: {len(attachments)}",
        reply_markup=ticket_attachments_done_keyboard(len(attachments)),
    )
    return ADDING_TICKET_ATTACHMENTS


async def finalize_ticket_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    draft = context.user_data.get("ticket_draft") or {}
    attachments = draft.get("attachments") or []
    safe_edit_message = _require_safe_edit_message()

    if not attachments:
        await safe_edit_message(query, "Нужно добавить хотя бы одно вложение перед сохранением.")
        return ADDING_TICKET_ATTACHMENTS

    title = str(draft.get("title") or "").strip()
    date_raw = str(draft.get("date") or "").strip()
    time_raw = str(draft.get("time") or "").strip()
    if not title or not _parse_date(date_raw) or not _parse_time(time_raw):
        await safe_edit_message(query, "Не удалось сохранить билет: проверь обязательные поля.")
        return SECTION

    data = storage.load()
    tickets = _tickets_root(data)

    afisha_item = normalize_event(
        {
            "id": make_id(),
            "title": title,
            "place": str(draft.get("place_route") or ""),
            "date": date_raw,
            "time": time_raw,
            "end_date": "",
            "end_time": "",
            "link": "",
            "status": "active",
            "notified_24h": False,
            "notified_morning": False,
        }
    )
    if afisha_item is None:
        await safe_edit_message(query, "Не удалось создать связанное событие Афиши. Проверь дату/время.")
        return SECTION

    ticket_id = make_id()
    afisha_item["ticket_id"] = ticket_id

    ticket = {
        "id": ticket_id,
        "title": title,
        "date": date_raw,
        "time": time_raw,
        "place_route": str(draft.get("place_route") or ""),
        "comment": str(draft.get("comment") or ""),
        "attachments": list(attachments),
        "afisha_id": afisha_item["id"],
    }
    tickets["active"].append(ticket)

    data["afisha"].append(afisha_item)
    data["afisha"] = sort_events(data.get("afisha", []))
    project_afisha_to_calendars(data, afisha_item)
    storage.save(data)

    context.user_data.pop("ticket_draft", None)
    await safe_edit_message(
        query,
        f"Билет сохранён:\n\n{_format_ticket_text(ticket)}",
        reply_markup=ticket_card_keyboard(ticket_id, "active", 0),
    )
    return SECTION


async def mark_ticket_used(update: Update, ticket_id: str, page: int) -> int:
    query = update.callback_query
    data = storage.load()
    tickets = _tickets_root(data)
    ticket = find_item(tickets.get("active", []), ticket_id)
    safe_edit_message = _require_safe_edit_message()
    if not ticket:
        await safe_edit_message(query, "Билет не найден в активных.")
        return SECTION

    moved_ticket = dict(ticket)
    delete_item_by_id(tickets["active"], ticket_id)
    tickets["used"].append(moved_ticket)

    afisha_id = str(moved_ticket.get("afisha_id") or "")
    if afisha_id:
        afisha_item = find_item(data.get("afisha", []), afisha_id)
        if afisha_item:
            afisha_item["status"] = "done"
            apply_afisha_status_update(data, afisha_item, "done")

    storage.save(data)
    await safe_edit_message(
        query,
        f"Билет отмечен как использованный:\n\n{_format_ticket_text(moved_ticket)}",
        reply_markup=ticket_card_keyboard(ticket_id, "used", page),
    )
    return SECTION


async def delete_ticket(update: Update, ticket_id: str, bucket: str, page: int) -> int:
    query = update.callback_query
    data = storage.load()
    tickets = _tickets_root(data)
    ticket = find_item(tickets.get(bucket, []), ticket_id)
    safe_edit_message = _require_safe_edit_message()
    if not ticket:
        await safe_edit_message(query, "Билет не найден.")
        return SECTION

    afisha_id = str(ticket.get("afisha_id") or "")
    if afisha_id:
        afisha_item = find_item(data.get("afisha", []), afisha_id)
        if afisha_item:
            delete_item_by_id(data["afisha"], afisha_id)
            apply_afisha_delete(data, afisha_item)

    delete_item_by_id(tickets[bucket], ticket_id)
    storage.save(data)
    return await show_tickets_list(update, bucket, page)


async def send_ticket_attachments(update: Update, context: ContextTypes.DEFAULT_TYPE, ticket_id: str, bucket: str, page: int) -> int:
    query = update.callback_query
    data = storage.load()
    ticket = find_item(_tickets_root(data).get(bucket, []), ticket_id)
    safe_edit_message = _require_safe_edit_message()
    if not ticket:
        await safe_edit_message(query, "Билет не найден.")
        return SECTION

    failures = 0
    for attachment in ticket.get("attachments", []):
        try:
            if attachment.get("kind") == "document":
                await context.bot.send_document(chat_id=query.message.chat_id, document=attachment.get("file_id"))
            else:
                await context.bot.send_photo(chat_id=query.message.chat_id, photo=attachment.get("file_id"))
        except TelegramError:
            failures += 1

    status_line = "Вложения отправлены." if failures == 0 else f"Отправлено с ошибками: {failures}."
    await safe_edit_message(
        query,
        f"{_format_ticket_text(ticket)}\n\n{status_line}",
        reply_markup=ticket_card_keyboard(ticket_id, bucket, page),
    )
    return SECTION


async def tickets_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    await remember_current_chat(update)
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) < 2 or parts[0] != "tickets":
        return SECTION

    action = parts[1]
    if action == "menu":
        context.user_data.pop("ticket_draft", None)
        return await show_tickets_menu(update)
    if action == "list" and len(parts) == 4:
        _, _, bucket, page_raw = parts
        if bucket not in {"active", "used"}:
            return SECTION
        return await show_tickets_list(update, bucket, int(page_raw))
    if action == "view" and len(parts) == 5:
        _, _, ticket_id, bucket, page_raw = parts
        if bucket not in {"active", "used"}:
            return SECTION
        return await show_ticket_card(update, ticket_id, bucket, int(page_raw))
    if action == "add" and len(parts) == 3 and parts[2] == "start":
        return await start_add_ticket(update, context)
    if action == "add" and len(parts) == 3 and parts[2] == "done_attachments":
        return await finalize_ticket_add(update, context)
    if action == "mark_used" and len(parts) == 4:
        _, _, ticket_id, page_raw = parts
        return await mark_ticket_used(update, ticket_id, int(page_raw))
    if action == "delete_confirm" and len(parts) == 5:
        _, _, ticket_id, bucket, page_raw = parts
        ticket_page = int(page_raw)
        data = storage.load()
        ticket = find_item(_tickets_root(data).get(bucket, []), ticket_id)
        safe_edit_message = _require_safe_edit_message()
        if not ticket:
            await safe_edit_message(query, "Билет не найден.")
            return SECTION
        await safe_edit_message(
            query,
            f"{_format_ticket_text(ticket)}\n\nТочно удалить?",
            reply_markup=ticket_delete_confirm_keyboard(ticket_id, bucket, ticket_page),
        )
        return SECTION
    if action == "delete" and len(parts) == 5:
        _, _, ticket_id, bucket, page_raw = parts
        return await delete_ticket(update, ticket_id, bucket, int(page_raw))
    if action == "attachments" and len(parts) == 6 and parts[2] == "send":
        _, _, _, ticket_id, bucket, page_raw = parts
        return await send_ticket_attachments(update, context, ticket_id, bucket, int(page_raw))

    return SECTION
