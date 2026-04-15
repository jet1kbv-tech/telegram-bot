from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
from bot.config import PAGE_SIZE
from bot.storage import (
    calendar_preview_text,
    delete_item_by_id,
    find_item,
    format_calendar_event_range,
    get_calendar_items,
    make_id,
    normalize_calendar_event,
    sort_calendar_events,
    storage,
)
from bot.utils import ensure_access, owner_label, paginate_items, remember_current_chat

_safe_edit_message: Callable[..., Awaitable[None]] | None = None
_main_menu_keyboard: Callable[[], InlineKeyboardMarkup] | None = None


def configure_calendar_handlers(
    *,
    safe_edit_message: Callable[..., Awaitable[None]],
    main_menu_keyboard: Callable[[], InlineKeyboardMarkup],
) -> None:
    global _safe_edit_message, _main_menu_keyboard
    _safe_edit_message = safe_edit_message
    _main_menu_keyboard = main_menu_keyboard


def _require_safe_edit_message() -> Callable[..., Awaitable[None]]:
    if _safe_edit_message is None:
        raise RuntimeError("Calendar handlers are not configured")
    return _safe_edit_message


def _require_main_menu_keyboard() -> Callable[[], InlineKeyboardMarkup]:
    if _main_menu_keyboard is None:
        raise RuntimeError("Calendar handlers are not configured")
    return _main_menu_keyboard


def build_calendar_menu_text() -> str:
    return "📅 Календарь\n\nВыбери, чей календарь открыть."


def calendar_owner_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📅 Календарь Саши", callback_data="cal_list|sasha|0")],
            [InlineKeyboardButton("📅 Календарь Вовы", callback_data="cal_list|vova|0")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]
    )


def build_calendar_owner_text(owner: str, items: list[dict[str, Any]], page: int) -> str:
    title = f"📅 Календарь {owner_label(owner)}"
    total_items = len(items)
    if total_items == 0:
        return f"{title}\n\nПока актуальных событий нет."
    start_num = page * PAGE_SIZE + 1
    end_num = min(total_items, start_num + PAGE_SIZE - 1)
    return (
        f"{title}\n\n"
        f"События {start_num}–{end_num} из {total_items}.\n"
        "Нажми на событие, чтобы открыть карточку или удалить его."
    )


def calendar_owner_keyboard(owner: str, items: list[dict[str, Any]], page: int) -> InlineKeyboardMarkup:
    page_items, current_page, total_pages = paginate_items(items, page)
    rows: list[list[InlineKeyboardButton]] = []
    for item in page_items:
        rows.append([
            InlineKeyboardButton(
                calendar_preview_text(item),
                callback_data=f"cal_view|{owner}|{item['id']}|{current_page}",
            )
        ])

    if total_pages > 1:
        row: list[InlineKeyboardButton] = []
        if current_page > 0:
            row.append(InlineKeyboardButton("⬅️", callback_data=f"cal_list|{owner}|{current_page - 1}"))
        row.append(InlineKeyboardButton(f"{current_page + 1}/{total_pages}", callback_data="noop"))
        if current_page < total_pages - 1:
            row.append(InlineKeyboardButton("➡️", callback_data=f"cal_list|{owner}|{current_page + 1}"))
        rows.append(row)

    rows.append([InlineKeyboardButton("➕ Добавить событие", callback_data=f"cal_add|{owner}")])
    rows.append([InlineKeyboardButton("⬅️ К выбору календаря", callback_data="calendar_menu")])
    rows.append([InlineKeyboardButton("🏠 В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def build_calendar_event_text(item: dict[str, Any]) -> str:
    lines = [
        f"📅 {item['title']}",
        f"Календарь: {owner_label(item['owner'])}",
        f"Когда: {format_calendar_event_range(item)}",
    ]
    if item.get("comment"):
        lines.append(f"Комментарий: {item['comment']}")
    return "\n".join(lines)


def calendar_event_keyboard(owner: str, item_id: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🗑️ Удалить", callback_data=f"cal_delete_confirm|{owner}|{item_id}|{page}")],
            [InlineKeyboardButton("⬅️ К списку", callback_data=f"cal_list|{owner}|{page}")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]
    )


def calendar_event_delete_confirm_keyboard(owner: str, item_id: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Да, удалить", callback_data=f"cal_delete|{owner}|{item_id}|{page}")],
            [InlineKeyboardButton("↩️ Нет, вернуться", callback_data=f"cal_view|{owner}|{item_id}|{page}")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]
    )


async def show_calendar_menu(update: Update) -> int:
    query = update.callback_query
    safe_edit_message = _require_safe_edit_message()
    await safe_edit_message(query, build_calendar_menu_text(), reply_markup=calendar_owner_menu_keyboard())
    return SECTION


async def show_calendar_owner(update: Update, owner: str, page: int = 0) -> int:
    query = update.callback_query
    data = storage.load()
    items = get_calendar_items(data, owner)
    _, current_page, _ = paginate_items(items, page)
    text = build_calendar_owner_text(owner, items, current_page)
    safe_edit_message = _require_safe_edit_message()
    await safe_edit_message(query, text, reply_markup=calendar_owner_keyboard(owner, items, current_page))
    return SECTION


async def show_calendar_owner_item(update: Update, owner: str, item_id: str, page: int) -> int:
    query = update.callback_query
    data = storage.load()
    item = find_item(data.get("calendars", {}).get(owner, []), item_id)
    safe_edit_message = _require_safe_edit_message()
    if not item:
        await safe_edit_message(
            query,
            "Событие не найдено.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К списку", callback_data=f"cal_list|{owner}|{page}")]]),
        )
        return SECTION
    await safe_edit_message(query, build_calendar_event_text(item), reply_markup=calendar_event_keyboard(owner, item_id, page))
    return SECTION


async def handle_calendar_delete_confirm(update: Update, owner: str, item_id: str, page: int) -> int:
    query = update.callback_query
    data = storage.load()
    item = find_item(data.get("calendars", {}).get(owner, []), item_id)
    safe_edit_message = _require_safe_edit_message()
    if not item:
        await safe_edit_message(
            query,
            "Не удалось найти событие для удаления.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]),
        )
        return SECTION
    await safe_edit_message(
        query,
        f"{build_calendar_event_text(item)}\n\nТочно удалить?",
        reply_markup=calendar_event_delete_confirm_keyboard(owner, item_id, page),
    )
    return SECTION


async def handle_calendar_delete(update: Update, owner: str, item_id: str, requested_page: int) -> int:
    query = update.callback_query
    data = storage.load()
    items = data.get("calendars", {}).get(owner, [])
    item = find_item(items, item_id)
    safe_edit_message = _require_safe_edit_message()
    if not item:
        await safe_edit_message(
            query,
            "Не удалось удалить: событие не найдено.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]),
        )
        return SECTION
    delete_item_by_id(items, item_id)
    storage.save(data)
    return await show_calendar_owner(update, owner, requested_page)


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
        await update.message.reply_text(
            "Не удалось понять, в какой календарь сохранять событие.",
            reply_markup=_require_main_menu_keyboard()(),
        )
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

    await update.message.reply_text(
        f"Событие сохранено:\n\n{build_calendar_event_text(normalized_item)}",
        reply_markup=calendar_event_keyboard(owner, normalized_item["id"], page=0),
    )
    return SECTION
