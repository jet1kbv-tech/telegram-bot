from datetime import datetime
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.states import (
    ADDING_EVENT_DATE,
    ADDING_EVENT_END_DATE,
    ADDING_EVENT_END_TIME,
    ADDING_EVENT_LINK,
    ADDING_EVENT_PLACE,
    ADDING_EVENT_TIME,
    ADDING_EVENT_TITLE,
    SECTION,
)
from bot.storage import make_id, normalize_event, sort_events, storage
from bot.storage import format_event_dt, is_event_actual
from bot.utils import ensure_access, item_status_label, remember_current_chat



_build_item_text: Callable[[str, dict[str, Any]], str] | None = None
_item_keyboard: Callable[..., InlineKeyboardMarkup] | None = None


def configure_afisha_handlers(
    *,
    build_item_text: Callable[[str, dict[str, Any]], str],
    item_keyboard: Callable[..., InlineKeyboardMarkup],
) -> None:
    global _build_item_text, _item_keyboard
    _build_item_text = build_item_text
    _item_keyboard = item_keyboard


def _require_build_item_text() -> Callable[[str, dict[str, Any]], str]:
    if _build_item_text is None:
        raise RuntimeError("Afisha handlers are not configured")
    return _build_item_text


def _require_item_keyboard() -> Callable[..., InlineKeyboardMarkup]:
    if _item_keyboard is None:
        raise RuntimeError("Afisha handlers are not configured")
    return _item_keyboard


def build_afisha_item_text(item: dict[str, Any]) -> str:
    lines = [
        f"🗓 {item['title']}",
        f"Статус: {item_status_label('afisha', item['status'])}",
        f"Когда: {format_event_dt(item)}",
    ]
    if item.get("place"):
        lines.append(f"Где: {item['place']}")
    if item.get("link"):
        lines.append(f"Ссылка: {item['link']}")
    return "\n".join(lines)


def build_afisha_list_button_text(item: dict[str, Any]) -> str:
    return f"{format_event_dt(item)} · {item['title']}"


def get_actual_afisha_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now()
    actual_items = [item for item in items if is_event_actual(item, now)]
    return sort_events(actual_items)


def afisha_empty_list_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Добавить событие", callback_data="add|afisha")],
            [InlineKeyboardButton("📅 Календарь", callback_data="calendar_menu")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="menu|afisha")],
        ]
    )


def apply_afisha_status_update(item: dict[str, Any], new_status: str) -> None:
    if new_status != "active":
        item["notified_24h"] = True


async def add_event_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Название события не должно быть пустым. Попробуй ещё раз:")
        return ADDING_EVENT_TITLE
    context.user_data["event_title"] = title
    await update.message.reply_text("Теперь отправь место. Если не нужно, напиши -")
    return ADDING_EVENT_PLACE


async def add_event_place(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    place = (update.message.text or "").strip()
    if place == "-":
        place = ""
    context.user_data["event_place"] = place
    await update.message.reply_text("Теперь отправь дату в формате ГГГГ-ММ-ДД, например 2026-04-05")
    return ADDING_EVENT_DATE


async def add_event_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    date_raw = (update.message.text or "").strip()
    try:
        datetime.strptime(date_raw, "%Y-%m-%d")
    except ValueError:
        await update.message.reply_text("Дата должна быть в формате ГГГГ-ММ-ДД. Попробуй ещё раз:")
        return ADDING_EVENT_DATE
    context.user_data["event_date"] = date_raw
    await update.message.reply_text("Теперь отправь время начала в формате ЧЧ:ММ, например 19:30")
    return ADDING_EVENT_TIME


async def add_event_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    time_raw = (update.message.text or "").strip()
    try:
        datetime.strptime(time_raw, "%H:%M")
    except ValueError:
        await update.message.reply_text("Время начала должно быть в формате ЧЧ:ММ. Попробуй ещё раз:")
        return ADDING_EVENT_TIME
    context.user_data["event_time"] = time_raw
    await update.message.reply_text("Теперь отправь дату окончания в формате ГГГГ-ММ-ДД. Если не нужно, напиши -")
    return ADDING_EVENT_END_DATE


async def add_event_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    end_date_raw = (update.message.text or "").strip()
    if end_date_raw == "-":
        end_date_raw = ""
    if end_date_raw:
        try:
            datetime.strptime(end_date_raw, "%Y-%m-%d")
        except ValueError:
            await update.message.reply_text("Дата окончания должна быть в формате ГГГГ-ММ-ДД или -. Попробуй ещё раз:")
            return ADDING_EVENT_END_DATE
        start_date = context.user_data.get("event_date", "")
        if end_date_raw < start_date:
            await update.message.reply_text("Дата окончания не может быть раньше даты начала. Попробуй ещё раз:")
            return ADDING_EVENT_END_DATE
    context.user_data["event_end_date"] = end_date_raw
    await update.message.reply_text("Теперь отправь время окончания в формате ЧЧ:ММ. Если не нужно, напиши -")
    return ADDING_EVENT_END_TIME


async def add_event_end_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    end_time_raw = (update.message.text or "").strip()
    if end_time_raw == "-":
        end_time_raw = ""
    if end_time_raw:
        try:
            datetime.strptime(end_time_raw, "%H:%M")
        except ValueError:
            await update.message.reply_text("Время окончания должно быть в формате ЧЧ:ММ или -. Попробуй ещё раз:")
            return ADDING_EVENT_END_TIME

        start_date = context.user_data.get("event_date", "")
        start_time = context.user_data.get("event_time", "")
        end_date = context.user_data.get("event_end_date") or start_date
        try:
            start_dt = datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(f"{end_date} {end_time_raw}", "%Y-%m-%d %H:%M")
        except ValueError:
            await update.message.reply_text("Не удалось распознать дату или время окончания. Попробуй ещё раз:")
            return ADDING_EVENT_END_TIME
        if end_dt < start_dt:
            await update.message.reply_text("Окончание не может быть раньше начала. Попробуй ещё раз:")
            return ADDING_EVENT_END_TIME

    context.user_data["event_end_time"] = end_time_raw
    await update.message.reply_text("Теперь отправь ссылку. Если ссылки нет, напиши -")
    return ADDING_EVENT_LINK


async def add_event_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    link = (update.message.text or "").strip()
    if link == "-":
        link = ""

    item = {
        "id": make_id(),
        "title": context.user_data.get("event_title", "Без названия"),
        "place": context.user_data.get("event_place", ""),
        "date": context.user_data.get("event_date", ""),
        "time": context.user_data.get("event_time", ""),
        "end_date": context.user_data.get("event_end_date", ""),
        "end_time": context.user_data.get("event_end_time", ""),
        "link": link,
        "status": "active",
        "notified_24h": False,
    }
    normalized_item = normalize_event(item)
    if normalized_item is None:
        await update.message.reply_text("Не удалось сохранить событие: проверь дату и время.")
        return SECTION

    data = storage.load()
    data["afisha"].append(normalized_item)
    data["afisha"] = sort_events(data["afisha"])
    storage.save(data)

    for key in ["event_title", "event_place", "event_date", "event_time", "event_end_date", "event_end_time"]:
        context.user_data.pop(key, None)
    context.user_data["active_section"] = "afisha"

    build_item_text = _require_build_item_text()
    item_keyboard = _require_item_keyboard()

    await update.message.reply_text(
        f"Событие сохранено:\n\n{build_item_text('afisha', normalized_item)}",
        reply_markup=item_keyboard("afisha", normalized_item, page=0),
    )
    return SECTION
