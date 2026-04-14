from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.states import (
    ADDING_CALENDAR_EVENT_COMMENT,
    ADDING_CALENDAR_EVENT_DATE,
    ADDING_CALENDAR_EVENT_END_TIME,
    ADDING_CALENDAR_EVENT_START_TIME,
    ADDING_CALENDAR_EVENT_TITLE,
    MENU,
    SECTION,
)
from bot.storage import make_id, normalize_calendar_event, sort_calendar_events, storage
from bot.utils import ensure_access, remember_current_chat


async def add_calendar_event_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Название события не должно быть пустым. Попробуй ещё раз:")
        return ADDING_CALENDAR_EVENT_TITLE
    context.user_data["calendar_event_title"] = title
    await update.message.reply_text("Теперь отправь дату в формате ГГГГ-ММ-ДД, например 2026-04-05")
    return ADDING_CALENDAR_EVENT_DATE


async def add_calendar_event_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    date_raw = (update.message.text or "").strip()
    try:
        datetime.strptime(date_raw, "%Y-%m-%d")
    except ValueError:
        await update.message.reply_text("Дата должна быть в формате ГГГГ-ММ-ДД. Попробуй ещё раз:")
        return ADDING_CALENDAR_EVENT_DATE
    context.user_data["calendar_event_date"] = date_raw
    await update.message.reply_text("Теперь отправь время начала в формате ЧЧ:ММ, например 19:30")
    return ADDING_CALENDAR_EVENT_START_TIME


async def add_calendar_event_start_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    start_time = (update.message.text or "").strip()
    try:
        datetime.strptime(start_time, "%H:%M")
    except ValueError:
        await update.message.reply_text("Время начала должно быть в формате ЧЧ:ММ. Попробуй ещё раз:")
        return ADDING_CALENDAR_EVENT_START_TIME
    context.user_data["calendar_event_start_time"] = start_time
    await update.message.reply_text("Теперь отправь время окончания в формате ЧЧ:ММ. Если оно не нужно, напиши -")
    return ADDING_CALENDAR_EVENT_END_TIME


async def add_calendar_event_end_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    end_time = (update.message.text or "").strip()
    if end_time == "-":
        end_time = ""
    if end_time:
        try:
            start_dt = datetime.strptime(
                f"{context.user_data.get('calendar_event_date', '')} {context.user_data.get('calendar_event_start_time', '')}",
                "%Y-%m-%d %H:%M",
            )
            end_dt = datetime.strptime(
                f"{context.user_data.get('calendar_event_date', '')} {end_time}",
                "%Y-%m-%d %H:%M",
            )
        except ValueError:
            await update.message.reply_text("Время окончания должно быть в формате ЧЧ:ММ. Попробуй ещё раз:")
            return ADDING_CALENDAR_EVENT_END_TIME
        if end_dt <= start_dt:
            await update.message.reply_text("Время окончания должно быть позже времени начала. Попробуй ещё раз:")
            return ADDING_CALENDAR_EVENT_END_TIME
    context.user_data["calendar_event_end_time"] = end_time
    await update.message.reply_text("Теперь отправь комментарий. Если не нужен, напиши -")
    return ADDING_CALENDAR_EVENT_COMMENT


async def add_calendar_event_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    comment = (update.message.text or "").strip()
    if comment == "-":
        comment = ""

    owner = str(context.user_data.get("calendar_owner") or "")
    if owner not in {"vova", "sasha"}:
        from main import main_menu_keyboard

        await update.message.reply_text("Не удалось понять, в какой календарь сохранять событие.", reply_markup=main_menu_keyboard())
        return MENU

    item = {
        "id": make_id(),
        "owner": owner,
        "title": context.user_data.get("calendar_event_title", "Без названия"),
        "date": context.user_data.get("calendar_event_date", ""),
        "start_time": context.user_data.get("calendar_event_start_time", ""),
        "end_time": context.user_data.get("calendar_event_end_time", ""),
        "comment": comment,
        "notified_24h": False,
    }
    normalized_item = normalize_calendar_event(item, owner)
    if normalized_item is None:
        await update.message.reply_text("Не удалось сохранить событие: проверь дату и время.")
        return SECTION

    data = storage.load()
    data.setdefault("calendars", {}).setdefault(owner, []).append(normalized_item)
    data["calendars"][owner] = sort_calendar_events(data["calendars"][owner])
    storage.save(data)

    for key in [
        "calendar_owner",
        "calendar_event_title",
        "calendar_event_date",
        "calendar_event_start_time",
        "calendar_event_end_time",
    ]:
        context.user_data.pop(key, None)

    from main import build_calendar_event_text, calendar_event_keyboard

    await update.message.reply_text(
        f"Событие сохранено:\n\n{build_calendar_event_text(normalized_item)}",
        reply_markup=calendar_event_keyboard(owner, normalized_item["id"], page=0),
    )
    return SECTION
