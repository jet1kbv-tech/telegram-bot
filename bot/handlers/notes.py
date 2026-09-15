from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import BOT_TIMEZONE
from bot.keyboards.notes import (note_card_keyboard, note_delete_keyboard, note_preview,
                                 notes_list_keyboard, notes_menu_keyboard)
from bot.services.notes_service import MAX_NOTE_TEXT_LENGTH, NotesService, NotesValidationError
from bot.states import ADDING_NOTE_TEXT, EDITING_NOTE_TEXT, SECTION
from bot.storage import storage
from bot.utils import clamp_page, ensure_access, get_wishlist_owner_by_user, remember_current_chat

_safe_edit: Callable[..., Awaitable[None]] | None = None
_service = NotesService(storage)
EDIT_SESSION = "notes_edit_session"


def configure_notes_handlers(*, safe_edit_message, service: NotesService | None = None) -> None:
    global _safe_edit, _service
    _safe_edit = safe_edit_message
    if service is not None:
        _service = service


def _format_timestamp(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=ZoneInfo(BOT_TIMEZONE))
        return moment.astimezone(ZoneInfo(BOT_TIMEZONE)).strftime("%d.%m.%Y · %H:%M")
    except (TypeError, ValueError):
        return ""


def note_card_text(note: dict[str, str]) -> str:
    created = _format_timestamp(note.get("created_at", ""))
    updated = _format_timestamp(note.get("updated_at", ""))
    if created and updated and created != updated:
        dates = f"Создано: {created}\nИзменено: {updated}"
    else:
        dates = created or updated
    return f"📝 {note['text']}" + (f"\n\n{dates}" if dates else "")


async def _show_list(update: Update, owner: str, page: int) -> int:
    notes = _service.list_notes(owner=owner)
    page = clamp_page(page, len(notes))
    text = "📝 Мои заметки\n\n" + ("Нажми на заметку, чтобы открыть её." if notes else "Пока пусто.")
    await _safe_edit(update.callback_query, text, reply_markup=notes_list_keyboard(notes, page))
    return SECTION


async def notes_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    query = update.callback_query
    await query.answer()
    parts = str(query.data or "").split(":")
    if not parts or parts[0] != "notes" or len(parts) < 2:
        return SECTION
    owner = get_wishlist_owner_by_user(update)
    action = parts[1]
    if action == "noop":
        return SECTION
    if action == "menu":
        await _safe_edit(query, "📝 Заметки\n\nЗдесь можно быстро сохранить мысль, идею\nили что-нибудь на потом.",
                         reply_markup=notes_menu_keyboard())
        return SECTION
    if action == "add":
        context.user_data.pop(EDIT_SESSION, None)
        await _safe_edit(query, "Отправь текст заметки:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад", callback_data="notes:menu")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]))
        return ADDING_NOTE_TEXT
    if action == "list" and len(parts) == 3:
        try:
            page = max(0, int(parts[2]))
        except ValueError:
            page = 0
        return await _show_list(update, owner, page)
    if action in {"view", "edit", "delete_confirm", "delete"} and len(parts) == 4:
        note_id = parts[2]
        try:
            page = max(0, int(parts[3]))
        except ValueError:
            page = 0
        note = _service.get_note(owner=owner, note_id=note_id)
        if note is None:
            await _safe_edit(query, "Заметка не найдена или недоступна.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ К заметкам", callback_data=f"notes:list:{page}")],
                [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
            ]))
            return SECTION
        if action == "view":
            await _safe_edit(query, note_card_text(note), reply_markup=note_card_keyboard(note_id, page))
            return SECTION
        if action == "edit":
            context.user_data[EDIT_SESSION] = {"note_id": note_id, "page": page}
            await _safe_edit(query, "Отправь новый текст заметки:", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ Отмена", callback_data=f"notes:view:{note_id}:{page}")],
                [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
            ]))
            return EDITING_NOTE_TEXT
        if action == "delete_confirm":
            await _safe_edit(query, f"📝 {note_preview(note['text'], 300)}\n\nТочно удалить заметку?",
                             reply_markup=note_delete_keyboard(note_id, page))
            return SECTION
        _service.delete_note(owner=owner, note_id=note_id)
        return await _show_list(update, owner, page)
    return SECTION


async def add_note_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    owner = get_wishlist_owner_by_user(update)
    try:
        note = _service.create_note(owner=owner, text=update.message.text or "")
    except NotesValidationError as error:
        message = (f"Заметка слишком длинная. Максимум {MAX_NOTE_TEXT_LENGTH} символов. Попробуй ещё раз:"
                   if str(error) == "text too long" else "Текст заметки не может быть пустым. Попробуй ещё раз:")
        await update.message.reply_text(message)
        return ADDING_NOTE_TEXT
    await update.message.reply_text(note_card_text(note), reply_markup=note_card_keyboard(note["id"], 0))
    return SECTION


async def edit_note_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    session = context.user_data.get(EDIT_SESSION)
    if not isinstance(session, dict):
        await update.message.reply_text("Заметка не найдена или недоступна.")
        return SECTION
    owner = get_wishlist_owner_by_user(update)
    try:
        note = _service.update_note(owner=owner, note_id=str(session.get("note_id") or ""),
                                    text=update.message.text or "")
    except NotesValidationError as error:
        message = (f"Заметка слишком длинная. Максимум {MAX_NOTE_TEXT_LENGTH} символов. Попробуй ещё раз:"
                   if str(error) == "text too long" else "Текст заметки не может быть пустым. Попробуй ещё раз:")
        await update.message.reply_text(message)
        return EDITING_NOTE_TEXT
    context.user_data.pop(EDIT_SESSION, None)
    if note is None:
        await update.message.reply_text("Заметка не найдена или недоступна.")
        return SECTION
    try:
        page = max(0, int(session.get("page", 0)))
    except (TypeError, ValueError):
        page = 0
    await update.message.reply_text(note_card_text(note), reply_markup=note_card_keyboard(note["id"], page))
    return SECTION
