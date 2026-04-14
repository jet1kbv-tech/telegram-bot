import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

from bot.config import (
    ALLOWED_USERS,
    BACKLOG_STATUSES,
    FILM_STATUSES,
    NOTIFICATION_CHECK_INTERVAL,
    NOTIFY_LOOKAHEAD_MAX,
    NOTIFY_LOOKAHEAD_MIN,
    PAGE_SIZE,
    SECTION_CONFIG,
)
from bot.handlers.backlog import add_backlog_description, add_backlog_title, configure_backlog_handlers
from bot.handlers.common import back_to_main, cancel, configure_common_handlers, noop, start, whoami
from bot.handlers.films import (
    add_film_comment,
    add_film_sasha_rating,
    add_film_title,
    add_film_vova_rating,
    configure_films_handlers,
    show_random_film,
)
from bot.handlers.leisure import add_leisure_comment, add_leisure_title, configure_leisure_handlers
from bot.handlers.afisha import (
    add_event_date,
    add_event_end_date,
    add_event_end_time,
    add_event_link,
    add_event_place,
    add_event_time,
    add_event_title,
)
from bot.handlers.calendar import (
    add_calendar_event_comment,
    add_calendar_event_date,
    add_calendar_event_end_time,
    add_calendar_event_start_time,
    add_calendar_event_title,
    configure_calendar_handlers,
    handle_calendar_delete,
    handle_calendar_delete_confirm,
    show_calendar_menu,
    show_calendar_owner,
    show_calendar_owner_item,
)
from bot.handlers.wishlist import (
    add_wishlist_comment,
    add_wishlist_link,
    add_wishlist_title,
    configure_wishlist_handlers,
)
from bot.states import (
    ADDING_BACKLOG_DESCRIPTION,
    ADDING_BACKLOG_TITLE,
    ADDING_CALENDAR_EVENT_COMMENT,
    ADDING_CALENDAR_EVENT_DATE,
    ADDING_CALENDAR_EVENT_END_TIME,
    ADDING_CALENDAR_EVENT_START_TIME,
    ADDING_CALENDAR_EVENT_TITLE,
    ADDING_EVENT_DATE,
    ADDING_EVENT_END_DATE,
    ADDING_EVENT_END_TIME,
    ADDING_EVENT_LINK,
    ADDING_EVENT_PLACE,
    ADDING_EVENT_TIME,
    ADDING_EVENT_TITLE,
    ADDING_FILM_COMMENT,
    ADDING_FILM_SASHA_RATING,
    ADDING_FILM_TITLE,
    ADDING_FILM_VOVA_RATING,
    ADDING_LEISURE_COMMENT,
    ADDING_LEISURE_TITLE,
    ADDING_WISHLIST_COMMENT,
    ADDING_WISHLIST_LINK,
    ADDING_WISHLIST_TITLE,
    MENU,
    SECTION,
)
from bot.storage import (
    delete_item_by_id,
    find_item,
    format_average_rating,
    format_event_dt,
    is_calendar_event_actual,
    is_event_actual,
    make_id,
    normalize_calendar_event,
    normalize_event,
    normalize_film,
    normalize_leisure,
    normalize_rating,
    normalize_wishlist,
    parse_calendar_event_end_dt,
    parse_calendar_event_start_dt,
    parse_event_dt,
    sort_calendar_events,
    sort_events,
    storage,
)
from bot.utils import (
    clamp_page,
    ensure_access,
    get_other_wishlist_owner,
    get_user_name,
    get_username,
    get_wishlist_owner_by_user,
    item_status_label,
    owner_label,
    paginate_items,
    remember_current_chat,
    reminder_forget_word,
    upsert_user_chat_id,
)

def section_menu_keyboard(section: str) -> InlineKeyboardMarkup:
    if section == "films":
        rows = [
            [InlineKeyboardButton("➕ Добавить", callback_data="add|films")],
            [InlineKeyboardButton("🎬 Непросмотренные", callback_data="list|films|want|0")],
            [InlineKeyboardButton("✅ Просмотренные", callback_data="list|films|watched|0")],
            [InlineKeyboardButton("🎲 Случайный фильм", callback_data="random|films")],
        ]
    elif section == "wishlist":
        rows = [
            [InlineKeyboardButton("➕ Добавить", callback_data="add|wishlist")],
            [InlineKeyboardButton("📋 Посмотреть список", callback_data="owners|wishlist")],
        ]
    elif section == "afisha":
        rows = [
            [InlineKeyboardButton("➕ Добавить событие", callback_data="add|afisha")],
            [InlineKeyboardButton("📋 Актуальные события", callback_data="list|afisha|0")],
        ]
    elif section == "backlog":
        rows = [
            [InlineKeyboardButton("➕ Добавить фичу", callback_data="add|backlog")],
            [InlineKeyboardButton("🧩 В планах", callback_data="list|backlog|todo|0")],
            [InlineKeyboardButton("✅ Реализовано", callback_data="list|backlog|done|0")],
        ]
    else:
        rows = [
            [InlineKeyboardButton("➕ Добавить", callback_data=f"add|{section}")],
            [InlineKeyboardButton("📋 Посмотреть список", callback_data=f"list|{section}|0")],
        ]

    rows.append([InlineKeyboardButton("⬅️ Назад в меню", callback_data="main")])
    return InlineKeyboardMarkup(rows)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎬 Фильмы", callback_data="menu|films")],
            [InlineKeyboardButton("🎁 Wishlist", callback_data="menu|wishlist")],
            [InlineKeyboardButton("✨ Досуг", callback_data="menu|leisure")],
            [InlineKeyboardButton("🗓 Афиша", callback_data="menu|afisha")],
            [InlineKeyboardButton("📅 Календарь", callback_data="calendar_menu")],
            [InlineKeyboardButton("🧩 Бэклог", callback_data="menu|backlog")],
        ]
    )


def wishlist_owner_keyboard(update: Update) -> InlineKeyboardMarkup:
    current_owner = get_wishlist_owner_by_user(update)
    current_name = get_user_name(update)
    other_owner = get_other_wishlist_owner(update)
    other_name = owner_label(other_owner)
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"📋 Вишлист: {current_name}", callback_data=f"list|wishlist|{current_owner}|0")],
            [InlineKeyboardButton(f"📋 Вишлист: {other_name}", callback_data=f"list|wishlist|{other_owner}|0")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="menu|wishlist")],
        ]
    )


def build_list_callback(section: str, page: int, owner: str | None = None, status_filter: str | None = None) -> str:
    if section == "wishlist" and owner:
        return f"list|wishlist|{owner}|{page}"
    if section in {"films", "backlog"} and status_filter:
        return f"list|{section}|{status_filter}|{page}"
    return f"list|{section}|{page}"


def build_view_callback(section: str, item_id: str, page: int, owner: str | None = None, status_filter: str | None = None) -> str:
    if section == "wishlist" and owner:
        return f"view|wishlist|{item_id}|{owner}|{page}"
    if section in {"films", "backlog"} and status_filter:
        return f"view|{section}|{item_id}|{status_filter}|{page}"
    return f"view|{section}|{item_id}|{page}"


def build_pagination_row(section: str, page: int, total_pages: int, owner: str | None = None, status_filter: str | None = None) -> list[InlineKeyboardButton]:
    if total_pages <= 1:
        return []
    row: list[InlineKeyboardButton] = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️", callback_data=build_list_callback(section, page - 1, owner, status_filter)))
    row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton("➡️", callback_data=build_list_callback(section, page + 1, owner, status_filter)))
    return row


def build_item_text(section: str, item: dict[str, Any]) -> str:
    if section == "films":
        lines = [
            f"🎬 {item['title']}",
            f"Статус: {item_status_label(section, item['status'])}",
            f"Добавил: {item.get('added_by', 'unknown')}",
        ]
        if item.get("status") == "watched":
            if item.get("sasha_rating") is not None:
                lines.append(f"Оценка Саши: {item['sasha_rating']}/10")
            if item.get("vova_rating") is not None:
                lines.append(f"Оценка Вовы: {item['vova_rating']}/10")
            average = format_average_rating(item)
            if average is not None:
                lines.append(f"Средний рейтинг: {average}/10")
            elif item.get("legacy_rating") is not None:
                lines.append(f"Рейтинг: {item['legacy_rating']}/10")
        if item.get("comment"):
            lines.append(f"Комментарий: {item['comment']}")
        return "\n".join(lines)

    if section == "wishlist":
        lines = [
            f"🎁 {item['title']}",
            f"Чей вишлист: {owner_label(item.get('owner', 'unknown'))}",
            f"Статус: {item_status_label(section, item['status'])}",
        ]
        if item.get("reserved_by"):
            lines.append(f"Кто отметил подарок: {item['reserved_by']}")
        if item.get("link"):
            lines.append(f"Ссылка: {item['link']}")
        if item.get("comment"):
            lines.append(f"Комментарий: {item['comment']}")
        return "\n".join(lines)

    if section == "leisure":
        lines = [
            f"✨ {item['title']}",
            f"Статус: {item_status_label(section, item['status'])}",
        ]
        if item.get("comment"):
            lines.append(f"Комментарий: {item['comment']}")
        return "\n".join(lines)

    if section == "afisha":
        lines = [
            f"🗓 {item['title']}",
            f"Статус: {item_status_label(section, item['status'])}",
            f"Когда: {format_event_dt(item)}",
        ]
        if item.get("place"):
            lines.append(f"Где: {item['place']}")
        if item.get("link"):
            lines.append(f"Ссылка: {item['link']}")
        return "\n".join(lines)

    if section == "backlog":
        lines = [
            f"🧩 {item['title']}",
            f"Статус: {item_status_label(section, item['status'])}",
        ]
        if item.get("description"):
            lines.append(f"Описание: {item['description']}")
        return "\n".join(lines)

    return "Элемент"


def build_list_text(section: str, items: list[dict[str, Any]], page: int, total_pages: int, owner: str | None = None, status_filter: str | None = None) -> str:
    title = SECTION_CONFIG[section]["title"]
    if section == "wishlist" and owner:
        title = f"🎁 Wishlist · {owner_label(owner)}"
    elif section == "films" and status_filter:
        title = f"🎬 Фильмы · {item_status_label(section, status_filter)}"
    elif section == "backlog" and status_filter:
        title = f"🧩 Бэклог · {item_status_label(section, status_filter)}"

    total_items = len(items)
    if total_items == 0:
        return f"{title}\n\n{SECTION_CONFIG[section]['empty_text']}"

    start_num = page * PAGE_SIZE + 1
    end_num = min(total_items, start_num + PAGE_SIZE - 1)
    return (
        f"{title}\n\n"
        f"Элементы {start_num}–{end_num} из {total_items}.\n"
        f"Нажми на пункт, чтобы открыть карточку, сменить статус или удалить его."
    )


def list_keyboard(section: str, items: list[dict[str, Any]], page: int, owner: str | None = None, status_filter: str | None = None) -> InlineKeyboardMarkup:
    page_items, current_page, total_pages = paginate_items(items, page)
    rows: list[list[InlineKeyboardButton]] = []

    for item in page_items:
        if section == "afisha":
            button_text = f"{format_event_dt(item)} · {item['title']}"
        else:
            button_text = f"{item['title']} · {item_status_label(section, item['status'])}"
        rows.append([
            InlineKeyboardButton(
                button_text,
                callback_data=build_view_callback(section, item['id'], current_page, owner, status_filter),
            )
        ])

    pagination_row = build_pagination_row(section, current_page, total_pages, owner, status_filter)
    if pagination_row:
        rows.append(pagination_row)

    if section == "wishlist":
        rows.append([InlineKeyboardButton("➕ Добавить", callback_data="add|wishlist")])
        rows.append([InlineKeyboardButton("⬅️ Выбрать другой вишлист", callback_data="owners|wishlist")])
    elif section == "films":
        rows.append([InlineKeyboardButton("🎲 Случайный фильм", callback_data="random|films")])
        rows.append([InlineKeyboardButton("➕ Добавить", callback_data="add|films")])
        rows.append([InlineKeyboardButton("⬅️ Назад к разделам фильмов", callback_data="menu|films")])
    elif section == "afisha":
        rows.append([InlineKeyboardButton("➕ Добавить событие", callback_data="add|afisha")])
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu|afisha")])
    elif section == "backlog":
        rows.append([InlineKeyboardButton("➕ Добавить фичу", callback_data="add|backlog")])
        rows.append([InlineKeyboardButton("⬅️ Назад к разделам бэклога", callback_data="menu|backlog")])
    else:
        rows.append([InlineKeyboardButton("➕ Добавить", callback_data=f"add|{section}")])
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"menu|{section}")])

    return InlineKeyboardMarkup(rows)


def build_back_to_list_callback(section: str, page: int, owner: str | None = None, status_filter: str | None = None) -> str:
    return build_list_callback(section, page, owner, status_filter)


def item_keyboard(section: str, item: dict[str, Any], page: int, owner: str | None = None, status_filter: str | None = None) -> InlineKeyboardMarkup:
    item_id = item["id"]

    if section == "films":
        toggle_to = "watched" if item["status"] == "want" else "want"
        toggle_text = "✅ Отметить как просмотренный" if toggle_to == "watched" else "↩️ Вернуть в непросмотренные"
    elif section == "wishlist":
        toggle_to = "gifted" if item["status"] == "active" else "active"
        toggle_text = "🎁 Отметить как подарено" if toggle_to == "gifted" else "↩️ Вернуть в актуальное"
    elif section == "afisha":
        toggle_to = "done" if item["status"] == "active" else "active"
        toggle_text = "✅ Отметить как выполнено" if toggle_to == "done" else "↩️ Вернуть в не выполнено"
    elif section == "backlog":
        toggle_to = "done" if item["status"] == "todo" else "todo"
        toggle_text = "✅ Отметить как реализовано" if toggle_to == "done" else "↩️ Вернуть в бэклог"
    else:
        toggle_to = "done" if item["status"] == "want" else "want"
        toggle_text = "✅ Отметить как сделано" if toggle_to == "done" else "↩️ Вернуть в планы"

    if section == "wishlist" and owner:
        status_callback = f"status|wishlist|{item_id}|{toggle_to}|{owner}|{page}"
        delete_confirm_callback = f"delete_confirm|wishlist|{item_id}|{owner}|{page}"
    elif section == "films" and status_filter:
        if item["status"] == "want" and toggle_to == "watched":
            status_callback = f"rate_start|films|{item_id}|{status_filter}|{page}"
        else:
            status_callback = f"status|films|{item_id}|{toggle_to}|{status_filter}|{page}"
        delete_confirm_callback = f"delete_confirm|films|{item_id}|{status_filter}|{page}"
    elif section == "backlog" and status_filter:
        status_callback = f"status|backlog|{item_id}|{toggle_to}|{status_filter}|{page}"
        delete_confirm_callback = f"delete_confirm|backlog|{item_id}|{status_filter}|{page}"
    else:
        status_callback = f"status|{section}|{item_id}|{toggle_to}|{page}"
        delete_confirm_callback = f"delete_confirm|{section}|{item_id}|{page}"

    back_callback = build_back_to_list_callback(section, page, owner, status_filter)

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(toggle_text, callback_data=status_callback)],
            [InlineKeyboardButton("🗑️ Удалить", callback_data=delete_confirm_callback)],
            [InlineKeyboardButton("⬅️ К списку", callback_data=back_callback)],
            [InlineKeyboardButton("🏠 В меню", callback_data="main")],
        ]
    )


def delete_confirm_keyboard(section: str, item_id: str, page: int, owner: str | None = None, status_filter: str | None = None) -> InlineKeyboardMarkup:
    if section == "wishlist" and owner:
        delete_callback = f"delete|wishlist|{item_id}|{owner}|{page}"
        back_callback = f"view|wishlist|{item_id}|{owner}|{page}"
    elif section in {"films", "backlog"} and status_filter:
        delete_callback = f"delete|{section}|{item_id}|{status_filter}|{page}"
        back_callback = f"view|{section}|{item_id}|{status_filter}|{page}"
    else:
        delete_callback = f"delete|{section}|{item_id}|{page}"
        back_callback = f"view|{section}|{item_id}|{page}"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Да, удалить", callback_data=delete_callback)],
            [InlineKeyboardButton("↩️ Нет, вернуться", callback_data=back_callback)],
        ]
    )


async def safe_edit_message(query, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup)
    except TelegramError as error:
        if "Message is not modified" in str(error):
            await query.answer()
            return
        raise


async def show_section_menu(update: Update, section: str) -> int:
    query = update.callback_query
    await safe_edit_message(query, f"{SECTION_CONFIG[section]['title']}\n\nВыберите действие:", reply_markup=section_menu_keyboard(section))
    return SECTION


async def show_list(update: Update, section: str, page: int = 0, owner: str | None = None, status_filter: str | None = None) -> int:
    query = update.callback_query
    data = storage.load()
    items = data.get(section, [])

    if section == "wishlist" and owner:
        items = [item for item in items if item.get("owner") == owner]
    elif section == "films" and status_filter in FILM_STATUSES:
        items = [item for item in items if item.get("status") == status_filter]
    elif section == "backlog" and status_filter in BACKLOG_STATUSES:
        items = [item for item in items if item.get("status") == status_filter]
    elif section == "afisha":
        now = datetime.now()
        items = [item for item in items if is_event_actual(item, now)]
        items = sort_events(items)

    _, current_page, total_pages = paginate_items(items, page)
    text = build_list_text(section, items, current_page, total_pages, owner, status_filter)

    if not items:
        if section == "wishlist":
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить в мой вишлист", callback_data="add|wishlist")],
                [InlineKeyboardButton("⬅️ Выбрать другой вишлист", callback_data="owners|wishlist")],
            ])
        elif section == "afisha":
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить событие", callback_data="add|afisha")],
                [InlineKeyboardButton("📅 Календарь", callback_data="calendar_menu")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu|afisha")],
            ])
        elif section == "backlog":
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить фичу", callback_data="add|backlog")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu|backlog")],
            ])
        else:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить", callback_data=f"add|{section}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data=f"menu|{section}")],
            ])
        await safe_edit_message(query, text, reply_markup=keyboard)
        return SECTION

    await safe_edit_message(query, text, reply_markup=list_keyboard(section, items, current_page, owner, status_filter))
    return SECTION


async def show_item(update: Update, section: str, item_id: str, page: int, owner: str | None = None, status_filter: str | None = None) -> int:
    query = update.callback_query
    data = storage.load()
    item = find_item(data.get(section, []), item_id)
    if not item:
        await safe_edit_message(
            query,
            "Элемент не найден.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К списку", callback_data=build_back_to_list_callback(section, page, owner, status_filter))]]),
        )
        return SECTION

    await safe_edit_message(query, build_item_text(section, item), reply_markup=item_keyboard(section, item, page, owner, status_filter))
    return SECTION


async def notify_other_user_about_wishlist_item(context: ContextTypes.DEFAULT_TYPE, update: Update, item: dict[str, Any]) -> None:
    username = get_username(update)
    other_username = None
    for allowed_username in ALLOWED_USERS:
        if allowed_username != username:
            other_username = allowed_username
            break
    if not other_username:
        return

    data = storage.load()
    chat_id = data.get("meta", {}).get("user_chats", {}).get(other_username)
    if not chat_id:
        logger.info("Не найден chat_id для %s — уведомление о wishlist пропущено", other_username)
        return

    owner = owner_label(item.get("owner", "unknown"))
    added_by = get_user_name(update)
    lines = [
        "🎁 В вишлист добавлен новый подарок!",
        "",
        f"Кому: {owner}",
        f"Что: {item['title']}",
        f"Добавил: {added_by}",
    ]
    if item.get("link"):
        lines.append(f"Ссылка: {item['link']}")
    if item.get("comment"):
        lines.append(f"Комментарий: {item['comment']}")

    try:
        await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))
    except TelegramError:
        logger.exception("Не удалось отправить уведомление второму участнику")


async def check_afisha_notifications(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = storage.load()
    now = datetime.now()
    changed = False
    user_chats = data.get("meta", {}).get("user_chats", {})

    for event in data.get("afisha", []):
        if event.get("status") != "active":
            continue

        event_dt = parse_event_dt(event)
        if not event_dt:
            continue

        if event_dt <= now:
            continue

        minutes_left = (event_dt - now).total_seconds() / 60
        if not (NOTIFY_LOOKAHEAD_MIN <= minutes_left <= NOTIFY_LOOKAHEAD_MAX):
            if minutes_left > NOTIFY_LOOKAHEAD_MAX and event.get("notified_24h"):
                event["notified_24h"] = False
                changed = True
            continue

        if event.get("notified_24h"):
            continue

        for username, profile in ALLOWED_USERS.items():
            chat_id = user_chats.get(username)
            if not chat_id:
                continue
            name = profile.get("name") or username
            forget_word = reminder_forget_word(username)
            text = (
                f"{name}, привет! Ты же не {forget_word}, что завтра у вас событие: {event['title']}\n"
                f"Когда: {format_event_dt(event)}"
            )
            if event.get("place"):
                text += f"\nГде: {event['place']}"
            if event.get("link"):
                text += f"\nСсылка: {event['link']}"
            try:
                await context.bot.send_message(chat_id=chat_id, text=text)
            except TelegramError:
                logger.exception("Не удалось отправить напоминание для %s", username)

        event["notified_24h"] = True
        changed = True

    for owner in ("vova", "sasha"):
        for event in data.get("calendars", {}).get(owner, []):
            event_dt = parse_calendar_event_start_dt(event)
            if not event_dt:
                continue
            if event_dt <= now:
                continue

            minutes_left = (event_dt - now).total_seconds() / 60
            if not (NOTIFY_LOOKAHEAD_MIN <= minutes_left <= NOTIFY_LOOKAHEAD_MAX):
                if minutes_left > NOTIFY_LOOKAHEAD_MAX and event.get("notified_24h"):
                    event["notified_24h"] = False
                    changed = True
                continue

            if event.get("notified_24h"):
                continue

            username = next((u for u, p in ALLOWED_USERS.items() if p.get("wishlist_owner") == owner), None)
            if not username:
                continue
            chat_id = user_chats.get(username)
            if not chat_id:
                continue
            profile = ALLOWED_USERS.get(username, {})
            name = profile.get("name") or owner_label(owner)
            forget_word = reminder_forget_word(username)
            text = (
                f"{name}, привет! Ты же не {forget_word}, что завтра у тебя событие в календаре: {event['title']}\n"
                f"Когда: {format_calendar_event_range(event)}"
            )
            if event.get("comment"):
                text += f"\nКомментарий: {event['comment']}"
            try:
                await context.bot.send_message(chat_id=chat_id, text=text)
            except TelegramError:
                logger.exception("Не удалось отправить календарное напоминание для %s", username)

            event["notified_24h"] = True
            changed = True

    if changed:
        storage.save(data)


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    await remember_current_chat(update)
    query = update.callback_query
    await query.answer()

    _, section = query.data.split("|", 1)
    context.user_data["active_section"] = section
    return await show_section_menu(update, section)


async def section_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    await remember_current_chat(update)
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|")
    action = parts[0]

    if action == "main":
        return await back_to_main(update, context)

    if action == "menu":
        section = parts[1]
        context.user_data["active_section"] = section
        return await show_section_menu(update, section)

    if action == "calendar_menu":
        return await show_calendar_menu(update)

    if action == "cal_list":
        _, owner, page_raw = parts
        return await show_calendar_owner(update, owner, int(page_raw))

    if action == "cal_view":
        _, owner, item_id, page_raw = parts
        return await show_calendar_owner_item(update, owner, item_id, int(page_raw))

    if action == "cal_add":
        _, owner = parts
        context.user_data["calendar_owner"] = owner
        await safe_edit_message(query, f"Календарь {owner_label(owner)}\n\nОтправь название события:")
        return ADDING_CALENDAR_EVENT_TITLE

    if action == "cal_delete_confirm":
        _, owner, item_id, page_raw = parts
        return await handle_calendar_delete_confirm(update, owner, item_id, int(page_raw))

    if action == "cal_delete":
        _, owner, item_id, page_raw = parts
        return await handle_calendar_delete(update, owner, item_id, int(page_raw))

    if action == "owners":
        await safe_edit_message(query, "Чей вишлист открыть?", reply_markup=wishlist_owner_keyboard(update))
        return SECTION

    if action == "random":
        return await show_random_film(update)

    if action == "add":
        section = parts[1]
        context.user_data["active_section"] = section
        if section == "films":
            await safe_edit_message(query, "Отправь название фильма одним сообщением:")
            return ADDING_FILM_TITLE
        if section == "wishlist":
            await safe_edit_message(query, "Отправь название подарка или пункта wishlist:\n\nОн автоматически попадёт в твой вишлист.")
            return ADDING_WISHLIST_TITLE
        if section == "leisure":
            await safe_edit_message(query, "Отправь идею для досуга одним сообщением:")
            return ADDING_LEISURE_TITLE
        if section == "afisha":
            await safe_edit_message(query, "Отправь название события:")
            return ADDING_EVENT_TITLE
        if section == "backlog":
            await safe_edit_message(query, "Отправь название фичи для бэклога:")
            return ADDING_BACKLOG_TITLE

    if action == "list":
        if parts[1] == "wishlist":
            _, _, owner, page_raw = parts
            return await show_list(update, "wishlist", int(page_raw), owner=owner)
        if parts[1] in {"films", "backlog"} and len(parts) == 4:
            _, section, status_filter, page_raw = parts
            return await show_list(update, section, int(page_raw), status_filter=status_filter)
        _, section, page_raw = parts
        return await show_list(update, section, int(page_raw))

    if action == "view":
        if parts[1] == "wishlist":
            _, _, item_id, owner, page_raw = parts
            return await show_item(update, "wishlist", item_id, int(page_raw), owner=owner)
        if parts[1] in {"films", "backlog"} and len(parts) == 5:
            _, section, item_id, status_filter, page_raw = parts
            return await show_item(update, section, item_id, int(page_raw), status_filter=status_filter)
        _, section, item_id, page_raw = parts
        return await show_item(update, section, item_id, int(page_raw))

    if action == "rate_start":
        _, _, item_id, status_filter, page_raw = parts
        page = int(page_raw)

        data = storage.load()
        item = find_item(data.get("films", []), item_id)
        if not item:
            await safe_edit_message(
                query,
                "Не удалось начать выставление оценок: фильм не найден.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К списку", callback_data=build_back_to_list_callback("films", page, status_filter=status_filter))]]),
            )
            return SECTION

        context.user_data["film_rating_item_id"] = item_id
        context.user_data["film_rating_page"] = page
        context.user_data["film_rating_status_filter"] = status_filter

        await query.message.reply_text(
            f"Фильм: {item['title']}\n\nКакую оценку Саша ставит фильму? Отправь число от 1 до 10."
        )
        return ADDING_FILM_SASHA_RATING

    if action == "status":
        if parts[1] == "wishlist":
            _, _, item_id, new_status, owner, page_raw = parts
            page = int(page_raw)
            section = "wishlist"
            status_filter = None
        elif parts[1] in {"films", "backlog"} and len(parts) == 6:
            _, section, item_id, new_status, status_filter, page_raw = parts
            page = int(page_raw)
            owner = None
        else:
            _, section, item_id, new_status, page_raw = parts
            page = int(page_raw)
            owner = None
            status_filter = None

        data = storage.load()
        item = find_item(data.get(section, []), item_id)
        if not item:
            await safe_edit_message(
                query,
                "Не удалось обновить статус: элемент не найден.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К списку", callback_data=build_back_to_list_callback(section, page, owner, status_filter))]]),
            )
            return SECTION

        item["status"] = new_status
        if section == "films" and new_status == "want":
            item["sasha_rating"] = None
            item["vova_rating"] = None
            item["legacy_rating"] = None
        if section == "wishlist":
            item["reserved_by"] = get_user_name(update) if new_status == "gifted" else ""
        if section == "afisha" and new_status != "active":
            item["notified_24h"] = True
        storage.save(data)
        await safe_edit_message(query, build_item_text(section, item), reply_markup=item_keyboard(section, item, page, owner, status_filter))
        return SECTION

    if action == "delete_confirm":
        if parts[1] == "wishlist":
            _, _, item_id, owner, page_raw = parts
            page = int(page_raw)
            section = "wishlist"
            status_filter = None
        elif parts[1] in {"films", "backlog"} and len(parts) == 5:
            _, section, item_id, status_filter, page_raw = parts
            page = int(page_raw)
            owner = None
        else:
            _, section, item_id, page_raw = parts
            page = int(page_raw)
            owner = None
            status_filter = None

        data = storage.load()
        item = find_item(data.get(section, []), item_id)
        if not item:
            await safe_edit_message(query, "Не удалось найти элемент для удаления.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В меню", callback_data="main")]]))
            return SECTION

        await safe_edit_message(query, f"{build_item_text(section, item)}\n\nТочно удалить?", reply_markup=delete_confirm_keyboard(section, item_id, page, owner, status_filter))
        return SECTION

    if action == "delete":
        if parts[1] == "wishlist":
            _, _, item_id, owner, page_raw = parts
            requested_page = int(page_raw)
            section = "wishlist"
            status_filter = None
        elif parts[1] in {"films", "backlog"} and len(parts) == 5:
            _, section, item_id, status_filter, page_raw = parts
            requested_page = int(page_raw)
            owner = None
        else:
            _, section, item_id, page_raw = parts
            requested_page = int(page_raw)
            owner = None
            status_filter = None

        data = storage.load()
        item = find_item(data.get(section, []), item_id)
        if not item:
            await safe_edit_message(query, "Не удалось удалить: элемент не найден.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В меню", callback_data="main")]]))
            return SECTION

        delete_item_by_id(data[section], item_id)
        storage.save(data)

        if section == "wishlist" and owner:
            items = [it for it in data["wishlist"] if it.get("owner") == owner]
            current_page = clamp_page(requested_page, len(items))
            text = f"🎁 Wishlist · {owner_label(owner)}\n\nЭлемент удалён." if items else f"🎁 Wishlist · {owner_label(owner)}\n\nЭлемент удалён. Список пуст."
            if items:
                await safe_edit_message(query, text, reply_markup=list_keyboard("wishlist", items, current_page, owner))
            else:
                await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Добавить в мой вишлист", callback_data="add|wishlist")],
                    [InlineKeyboardButton("⬅️ Выбрать другой вишлист", callback_data="owners|wishlist")],
                ]))
            return SECTION

        if section == "afisha":
            return await show_list(update, "afisha", requested_page)
        if section in {"films", "backlog"} and status_filter:
            return await show_list(update, section, requested_page, status_filter=status_filter)

        section_items = data[section]
        current_page = clamp_page(requested_page, len(section_items))
        text = f"{SECTION_CONFIG[section]['title']}\n\nЭлемент удалён." if section_items else f"{SECTION_CONFIG[section]['title']}\n\nЭлемент удалён. Список пуст."
        if section_items:
            await safe_edit_message(query, text, reply_markup=list_keyboard(section, section_items, current_page))
        else:
            await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить", callback_data=f"add|{section}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data=f"menu|{section}")],
            ]))
        return SECTION

    return SECTION




def build_app() -> Application:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Переменная окружения BOT_TOKEN не установлена.")

    app = Application.builder().token(token).build()
    configure_common_handlers(main_menu_keyboard=main_menu_keyboard, safe_edit_message=safe_edit_message)
    configure_backlog_handlers(build_item_text=build_item_text, item_keyboard=item_keyboard)
    configure_films_handlers(
        safe_edit_message=safe_edit_message,
        build_item_text=build_item_text,
        item_keyboard=item_keyboard,
        main_menu_keyboard=main_menu_keyboard,
    )
    configure_leisure_handlers(build_item_text=build_item_text, item_keyboard=item_keyboard)
    configure_wishlist_handlers(
        build_item_text=build_item_text,
        item_keyboard=item_keyboard,
        notify_other_user_about_wishlist_item=notify_other_user_about_wishlist_item,
    )

    configure_calendar_handlers(
        safe_edit_message=safe_edit_message,
        main_menu_keyboard=main_menu_keyboard,
    )

    if app.job_queue is not None:
        app.job_queue.run_repeating(check_afisha_notifications, interval=NOTIFICATION_CHECK_INTERVAL, first=30, name="afisha_notifications")
    else:
        logger.warning("JobQueue недоступна. Для уведомлений за день до события нужен APScheduler в requirements.")

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [
                CallbackQueryHandler(back_to_main, pattern=r"^main$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|(films|wishlist|leisure|afisha|backlog)$"),
                CallbackQueryHandler(section_router),
            ],
            SECTION: [
                CallbackQueryHandler(noop, pattern=r"^noop$"),
                CallbackQueryHandler(back_to_main, pattern=r"^main$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|(films|wishlist|leisure|afisha|backlog)$"),
                CallbackQueryHandler(section_router),
            ],
            ADDING_FILM_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_film_title)],
            ADDING_FILM_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_film_comment)],
            ADDING_FILM_SASHA_RATING: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_film_sasha_rating)],
            ADDING_FILM_VOVA_RATING: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_film_vova_rating)],
            ADDING_CALENDAR_EVENT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_calendar_event_title)],
            ADDING_CALENDAR_EVENT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_calendar_event_date)],
            ADDING_CALENDAR_EVENT_START_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_calendar_event_start_time)],
            ADDING_CALENDAR_EVENT_END_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_calendar_event_end_time)],
            ADDING_CALENDAR_EVENT_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_calendar_event_comment)],
            ADDING_BACKLOG_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_backlog_title)],
            ADDING_BACKLOG_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_backlog_description)],
            ADDING_WISHLIST_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wishlist_title)],
            ADDING_WISHLIST_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wishlist_link)],
            ADDING_WISHLIST_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wishlist_comment)],
            ADDING_LEISURE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_leisure_title)],
            ADDING_LEISURE_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_leisure_comment)],
            ADDING_EVENT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_title)],
            ADDING_EVENT_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_place)],
            ADDING_EVENT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_date)],
            ADDING_EVENT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_time)],
            ADDING_EVENT_END_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_end_date)],
            ADDING_EVENT_END_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_end_time)],
            ADDING_EVENT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_link)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(conv_handler)
    return app


if __name__ == "__main__":
    application = build_app()
    application.run_polling(drop_pending_updates=True)
