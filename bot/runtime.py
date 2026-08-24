import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

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

from bot.logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from bot.config import (
    AFISHA_MORNING_END_HOUR,
    AFISHA_MORNING_START_HOUR,
    ALLOWED_USERS,
    BACKLOG_STATUSES,
    BOT_TIMEZONE,
    FILM_STATUSES,
    NOTIFICATION_CHECK_INTERVAL,
    NOTIFY_LOOKAHEAD_MAX,
    NOTIFY_LOOKAHEAD_MIN,
    TRIP_REMINDER_GRACE_MINUTES,
    SECTION_CONFIG,
)
from bot.services.notification_enrichment import build_notification_context, render_notification_enrichment
from bot.services.proactive_trip_reminders import scan_trip_reminders
from bot.services.weather import WeatherProvider
from bot.handlers.backlog import add_backlog_description, add_backlog_title, configure_backlog_handlers
from bot.handlers.common import back_to_main, cancel, configure_common_handlers, noop, start, whoami
from bot.handlers.films import (
    add_film_comment,
    add_film_title,
    clear_film_conversation_data,
    configure_films_handlers,
    show_random_film,
)
from bot.handlers.film_operations import clear_film_reaction, delete_film, set_film_reaction, set_film_status
from bot.handlers.leisure import add_leisure_comment, add_leisure_title, configure_leisure_handlers
from bot.handlers.afisha import (
    afisha_empty_list_keyboard,
    add_event_date,
    add_event_end_date,
    add_event_end_time,
    add_event_link,
    add_event_place,
    add_event_time,
    add_event_title,
    afisha_edit_menu_keyboard,
    apply_afisha_delete,
    apply_afisha_status_update,
    edit_afisha_date,
    edit_afisha_time,
    get_actual_afisha_items,
)
from bot.handlers.calendar import (
    add_calendar_event_comment,
    add_calendar_event_date,
    add_calendar_event_end_time,
    add_calendar_event_start_time,
    add_calendar_event_title,
    configure_calendar_handlers,
    edit_calendar_date,
    edit_calendar_time,
    handle_calendar_edit_field,
    handle_calendar_edit_start,
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
    EDITING_CALENDAR_DATE,
    EDITING_CALENDAR_TIME,
    ADDING_EVENT_DATE,
    ADDING_EVENT_END_DATE,
    ADDING_EVENT_END_TIME,
    ADDING_EVENT_LINK,
    ADDING_EVENT_PLACE,
    ADDING_EVENT_TIME,
    ADDING_EVENT_TITLE,
    EDITING_AFISHA_DATE,
    EDITING_AFISHA_TIME,
    ADDING_FILM_COMMENT,
    ADDING_FILM_TITLE,
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
    format_event_dt,
    is_calendar_event_actual,
    make_id,
    normalize_calendar_event,
    normalize_event,
    normalize_film,
    normalize_leisure,
    normalize_wishlist,
    parse_calendar_event_end_dt,
    parse_calendar_event_start_dt,
    parse_event_dt,
    sort_calendar_events,
    storage,
)
from bot.utils import (
    clamp_page,
    ensure_access,
    get_user_name,
    get_username,
    get_wishlist_owner_by_user,
    owner_label,
    paginate_items,
    remember_current_chat,
    reminder_forget_word,
    upsert_user_chat_id,
)

from bot.keyboards.common import (
    build_back_to_list_callback,
    delete_confirm_keyboard,
    item_keyboard,
    list_keyboard,
    activity_menu_keyboard,
    main_menu_keyboard,
    section_menu_keyboard,
    wishlist_owner_keyboard,
)
from bot.ui.common import build_item_text, build_list_text

_notification_weather_provider: WeatherProvider | None = None


def configure_notification_enrichment(weather_provider: WeatherProvider) -> None:
    """Install the shared cached provider used at the existing send boundary."""
    global _notification_weather_provider
    _notification_weather_provider = weather_provider


async def check_trip_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Single hourly actor-scoped scan; no per-trip scheduler jobs."""
    await scan_trip_reminders(storage=storage, bot=context.bot, actors=ALLOWED_USERS,
        timezone=BOT_TIMEZONE, grace=timedelta(minutes=TRIP_REMINDER_GRACE_MINUTES),
        now=datetime.now(ZoneInfo(BOT_TIMEZONE)), weather_provider=_notification_weather_provider)


async def _enrich_afisha_reminder(core_text: str, data: dict[str, Any], event: dict[str, Any],
                                  actor_key: str, now: datetime) -> str:
    if _notification_weather_provider is None:
        return core_text
    try:
        enrichment = await build_notification_context(data, event_id=str(event.get("id") or ""),
            actor_key=actor_key, now=now, timezone=BOT_TIMEZONE,
            weather_provider=_notification_weather_provider)
        suffix = render_notification_enrichment(enrichment)
        logger.info("notification_enrichment event_resolved=%s trip_linked=%s weather_attempted=%s included=%s",
            enrichment.event_context is not None, enrichment.trip_context is not None,
            enrichment.weather_attempted, bool(suffix))
        if "weather_failed" in enrichment.enrichment_reasons:
            logger.warning("notification_enrichment weather_failed=true core_preserved=true")
        return f"{core_text}\n\n{suffix}" if suffix else core_text
    except Exception:
        logger.warning("notification_enrichment failed=true core_preserved=true", exc_info=True)
        return core_text

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
    unrated_count = None
    if section == "films":
        actor_key = get_wishlist_owner_by_user(update)
        unrated_count = len(unrated_watched_films(storage.load().get("films", []), actor_key))
    await safe_edit_message(
        query,
        f"{SECTION_CONFIG[section]['title']}\n\nВыберите действие:",
        reply_markup=section_menu_keyboard(section, unrated_watched_count=unrated_count),
    )
    return SECTION


FILM_RATING_SESSION_KEY = "film_rating_session"


def unrated_watched_films(films: list[dict[str, Any]], actor_key: str) -> list[dict[str, Any]]:
    """Return the actor's watched+unknown films in stable storage order."""
    return [
        film for film in films
        if film.get("status") == "watched"
        and actor_key not in (film.get("reactions") if isinstance(film.get("reactions"), dict) else {})
    ]


def clear_film_rating_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(FILM_RATING_SESSION_KEY, None)


def film_backlog_keyboard(film_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❤️ Понравилось", callback_data=f"film_backlog|react|{film_id}|like")],
        [InlineKeyboardButton("😐 Нормально", callback_data=f"film_backlog|react|{film_id}|neutral")],
        [InlineKeyboardButton("👎 Не понравилось", callback_data=f"film_backlog|react|{film_id}|dislike")],
        [InlineKeyboardButton("⏭ Пропустить", callback_data=f"film_backlog|skip|{film_id}")],
        [InlineKeyboardButton("❌ Закончить", callback_data="film_backlog|finish")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])


async def show_next_film_backlog_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    actor_key = get_wishlist_owner_by_user(update)
    session = context.user_data.get(FILM_RATING_SESSION_KEY)
    if not isinstance(session, dict) or session.get("actor_key") != actor_key:
        session = {"actor_key": actor_key, "visited_ids": [], "rated_count": 0, "offered_count": 0}
        context.user_data[FILM_RATING_SESSION_KEY] = session
    visited = set(session["visited_ids"])
    candidates = unrated_watched_films(storage.load().get("films", []), actor_key)
    film = next((item for item in candidates if item.get("id") not in visited), None)
    if film is None:
        rated_count = int(session.get("rated_count", 0))
        clear_film_rating_session(context)
        text = "Ты уже оценил все просмотренные фильмы." if int(session.get("offered_count", 0)) == 0 else f"Готово. Оценено: {rated_count}"
        await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ К фильмам", callback_data="menu|films")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]))
        return SECTION
    session["offered_count"] = int(session.get("offered_count", 0)) + 1
    session["current_film_id"] = str(film["id"])
    await safe_edit_message(
        query,
        "⭐ Оценка просмотренных\n\n" + build_item_text("films", film),
        reply_markup=film_backlog_keyboard(str(film["id"])),
    )
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
        items = get_actual_afisha_items(items)

    _, current_page, total_pages = paginate_items(items, page)
    text = build_list_text(section, items, current_page, total_pages, owner, status_filter)

    if not items:
        if section == "wishlist":
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить в мой вишлист", callback_data="add|wishlist")],
                [InlineKeyboardButton("⬅️ Выбрать другой вишлист", callback_data="owners|wishlist")],
            ])
        elif section == "afisha":
            keyboard = afisha_empty_list_keyboard()
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

    actor_key = get_wishlist_owner_by_user(update) if section in {"films", "afisha"} else None
    await safe_edit_message(query, build_item_text(section, item), reply_markup=item_keyboard(section, item, page, owner, status_filter, actor_key))
    return SECTION


def film_reaction_followup_keyboard(item_id: str, status_filter: str, page: int) -> InlineKeyboardMarkup:
    """Optional post-watch preference prompt using the shared reaction callbacks."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❤️ Понравилось", callback_data=f"film_reaction|{item_id}|like|{status_filter}|{page}")],
        [InlineKeyboardButton("😐 Нормально", callback_data=f"film_reaction|{item_id}|neutral|{status_filter}|{page}")],
        [InlineKeyboardButton("👎 Не понравилось", callback_data=f"film_reaction|{item_id}|dislike|{status_filter}|{page}")],
        [InlineKeyboardButton("Пропустить", callback_data=f"film_reaction_skip|{item_id}|{status_filter}|{page}")],
    ])


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


async def notify_other_user_about_calendar_item(context: ContextTypes.DEFAULT_TYPE, update: Update, item: dict[str, Any]) -> None:
    if item.get("source") != "manual":
        return

    owner = str(item.get("owner") or "")
    username = get_username(update)
    other_username = next(
        (
            allowed_username
            for allowed_username, profile in ALLOWED_USERS.items()
            if allowed_username != username and profile.get("wishlist_owner") != owner
        ),
        None,
    )
    if not other_username:
        return

    data = storage.load()
    chat_id = data.get("meta", {}).get("user_chats", {}).get(other_username)
    if not chat_id:
        logger.info("Не найден chat_id для %s — уведомление о календаре пропущено", other_username)
        return

    added_by = get_user_name(update)
    date = str(item.get("date") or "")
    start_time = str(item.get("start_time") or "")
    end_time = str(item.get("end_time") or "")
    time_range = f"{date} {start_time}".strip()
    if end_time:
        time_range = f"{time_range}–{end_time}".strip()
    lines = [
        "📅 Новые планы!",
        "",
        f"Календарь: {owner_label(owner)}",
        f"Событие: {item.get('title', 'Без названия')}",
        f"Когда: {time_range}",
        f"Добавил(а): {added_by}",
    ]
    if item.get("comment"):
        lines.append(f"Комментарий: {item['comment']}")

    try:
        await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))
    except TelegramError:
        logger.exception("Не удалось отправить уведомление второму участнику о календарном событии")


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

        is_today = event_dt.date() == now.date()
        in_morning_window = AFISHA_MORNING_START_HOUR <= now.hour < AFISHA_MORNING_END_HOUR
        if is_today and in_morning_window and not event.get("notified_morning"):
            for username, profile in ALLOWED_USERS.items():
                chat_id = user_chats.get(username)
                if not chat_id:
                    continue
                name = profile.get("name") or username
                forget_word = reminder_forget_word(username)
                text = (
                    f"{name}, доброе утро! Ты же не {forget_word}, что сегодня у вас событие: {event['title']}\n"
                    f"Когда: {format_event_dt(event)}"
                )
                if event.get("place"):
                    text += f"\nГде: {event['place']}"
                if event.get("link"):
                    text += f"\nСсылка: {event['link']}"
                text = await _enrich_afisha_reminder(text, data, event,
                    str(profile.get("wishlist_owner") or ""), now)
                try:
                    await context.bot.send_message(chat_id=chat_id, text=text)
                except TelegramError:
                    logger.exception("Не удалось отправить утреннее напоминание для %s", username)
            event["notified_morning"] = True
            changed = True

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
            text = await _enrich_afisha_reminder(text, data, event,
                str(profile.get("wishlist_owner") or ""), now)
            try:
                await context.bot.send_message(chat_id=chat_id, text=text)
            except TelegramError:
                logger.exception("Не удалось отправить напоминание для %s", username)

        event["notified_24h"] = True
        changed = True

    for owner in ("vova", "sasha"):
        for event in data.get("calendars", {}).get(owner, []):
            if event.get("source") == "afisha":
                continue
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
    clear_film_rating_session(context)
    context.user_data.pop("film_recommendation_session", None)
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

    if action == "film_backlog":
        operation = parts[1] if len(parts) > 1 else ""
        actor_key = get_wishlist_owner_by_user(update)
        if operation == "start":
            clear_film_rating_session(context)
            context.user_data[FILM_RATING_SESSION_KEY] = {
                "actor_key": actor_key, "visited_ids": [], "rated_count": 0, "offered_count": 0,
            }
            return await show_next_film_backlog_item(update, context)
        session = context.user_data.get(FILM_RATING_SESSION_KEY)
        if not isinstance(session, dict) or session.get("actor_key") != actor_key:
            clear_film_rating_session(context)
            await safe_edit_message(query, "Сессия оценки устарела.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐ Начать заново", callback_data="film_backlog|start")],
                [InlineKeyboardButton("⬅️ К фильмам", callback_data="menu|films")],
            ]))
            return SECTION
        if operation == "finish":
            rated_count = int(session.get("rated_count", 0))
            clear_film_rating_session(context)
            await safe_edit_message(query, f"Готово. Оценено: {rated_count}", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ К фильмам", callback_data="menu|films")],
                [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
            ]))
            return SECTION
        if operation in {"react", "skip"} and len(parts) >= 3:
            film_id = parts[2]
            visited_ids = session.setdefault("visited_ids", [])
            eligible_ids = {str(item.get("id")) for item in unrated_watched_films(storage.load().get("films", []), actor_key)}
            if film_id != session.get("current_film_id") or film_id in visited_ids or film_id not in eligible_ids:
                return await show_next_film_backlog_item(update, context)
            if operation == "react":
                reaction = parts[3] if len(parts) > 3 else ""
                if reaction not in {"like", "neutral", "dislike"}:
                    return await show_next_film_backlog_item(update, context)
                result = set_film_reaction(film_id, actor_key, reaction)
                if result.found:
                    session["rated_count"] = int(session.get("rated_count", 0)) + 1
            visited_ids.append(film_id)
            session.pop("current_film_id", None)
            return await show_next_film_backlog_item(update, context)
        return await show_next_film_backlog_item(update, context)

    if action == "film_reaction":
        _, item_id, reaction, status_filter, page_raw = parts
        actor_key = get_wishlist_owner_by_user(update)
        result = clear_film_reaction(item_id, actor_key) if reaction == "clear" else set_film_reaction(item_id, actor_key, reaction)
        if not result.found:
            await safe_edit_message(query, "Фильм не найден.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ К списку", callback_data=f"list|films|{status_filter}|{page_raw}")]
            ]))
            return SECTION
        item = result.film
        await safe_edit_message(
            query,
            build_item_text("films", item),
            reply_markup=item_keyboard("films", item, int(page_raw), status_filter=status_filter, actor_key=actor_key),
        )
        return SECTION

    if action == "film_reaction_skip":
        _, item_id, status_filter, page_raw = parts
        item = find_item(storage.load().get("films", []), item_id)
        if not item:
            return await show_list(update, "films", int(page_raw), status_filter=status_filter)
        actor_key = get_wishlist_owner_by_user(update)
        await safe_edit_message(query, build_item_text("films", item), reply_markup=item_keyboard(
            "films", item, int(page_raw), status_filter="watched", actor_key=actor_key,
        ))
        return SECTION

    if action in {"main", "menu:main"}:
        clear_film_rating_session(context)
        return await back_to_main(update, context)

    if query.data == "activity:menu":
        await safe_edit_message(query, "Чем займемся", reply_markup=activity_menu_keyboard())
        return SECTION

    if action == "menu":
        clear_film_rating_session(context)
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

    if action == "cal_edit":
        _, owner, item_id, page_raw = parts
        return await handle_calendar_edit_start(update, owner, item_id, int(page_raw))

    if action == "cal_edit_field":
        _, owner, item_id, field, page_raw = parts
        return await handle_calendar_edit_field(update, context, owner, item_id, field, int(page_raw))

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
            clear_film_conversation_data(context)
            await safe_edit_message(query, "Введите название фильма или сериала:")
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

    if action == "af_edit":
        _, item_id, page_raw = parts
        page = int(page_raw)
        data = storage.load()
        item = find_item(data.get("afisha", []), item_id)
        if not item:
            await safe_edit_message(
                query,
                "Не удалось открыть редактирование: событие не найдено.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К списку", callback_data=f"list|afisha|{page}")]]),
            )
            return SECTION
        await safe_edit_message(
            query,
            f"{build_item_text('afisha', item)}\n\nВыбери, что изменить:",
            reply_markup=afisha_edit_menu_keyboard(item_id, page),
        )
        return SECTION

    if action == "af_edit_field":
        _, item_id, field, page_raw = parts
        page = int(page_raw)
        data = storage.load()
        item = find_item(data.get("afisha", []), item_id)
        if not item:
            await safe_edit_message(
                query,
                "Не удалось открыть редактирование: событие не найдено.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К списку", callback_data=f"list|afisha|{page}")]]),
            )
            return SECTION
        context.user_data["editing_afisha_item_id"] = item_id
        context.user_data["editing_afisha_page"] = page
        if field == "date":
            await safe_edit_message(query, f"{build_item_text('afisha', item)}\n\nОтправь новую дату в формате ГГГГ-ММ-ДД:")
            return EDITING_AFISHA_DATE
        if field == "time":
            await safe_edit_message(query, f"{build_item_text('afisha', item)}\n\nОтправь новое время начала в формате ЧЧ:ММ:")
            return EDITING_AFISHA_TIME
        await safe_edit_message(
            query,
            "Не удалось понять, какое поле нужно изменить.",
            reply_markup=afisha_edit_menu_keyboard(item_id, page),
        )
        return SECTION

    if action == "rate_start":
        # Compatibility for buttons sent before Films v2: mark watched without
        # collecting or modifying the retained legacy personal-rating fields.
        _, _, item_id, status_filter, page_raw = parts
        page = int(page_raw)

        data = storage.load()
        item = find_item(data.get("films", []), item_id)
        if not item:
            await safe_edit_message(
                query,
                "Не удалось обновить статус: фильм не найден.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К списку", callback_data=build_back_to_list_callback("films", page, status_filter=status_filter))]]),
            )
            return SECTION

        result = set_film_status(item_id, "watched")
        item = result.film or item
        await safe_edit_message(query, "Как тебе фильм?", reply_markup=film_reaction_followup_keyboard(item_id, "watched", page))
        return SECTION

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

        if section == "films":
            result = set_film_status(item_id, new_status)
            item = result.film or item
        else:
            item["status"] = new_status
        if section == "wishlist":
            item["reserved_by"] = get_user_name(update) if new_status == "gifted" else ""
        if section == "afisha":
            apply_afisha_status_update(data, item, new_status)
        if section != "films":
            storage.save(data)
        if section == "films" and new_status == "watched":
            await safe_edit_message(query, "Как тебе фильм?", reply_markup=film_reaction_followup_keyboard(item_id, "watched", page))
            return SECTION
        actor_key = get_wishlist_owner_by_user(update) if section in {"films", "afisha"} else None
        await safe_edit_message(query, build_item_text(section, item), reply_markup=item_keyboard(section, item, page, owner, status_filter, actor_key))
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
            await safe_edit_message(query, "Не удалось найти элемент для удаления.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]))
            return SECTION

        warning = ""
        if section == "afisha":
            from bot.services.event_attachments import get_attachments_for_event
            count = len(get_attachments_for_event(data, "afisha", item_id))
            if count:
                warning = f"\n\nК событию прикреплено {count} документов.\nПри удалении события они тоже исчезнут из бота."
        await safe_edit_message(query, f"{build_item_text(section, item)}{warning}\n\nТочно удалить?", reply_markup=delete_confirm_keyboard(section, item_id, page, owner, status_filter))
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
            await safe_edit_message(query, "Не удалось удалить: элемент не найден.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]))
            return SECTION

        if section == "afisha":
            # Cascade while the source parent still exists; the attachment
            # service intentionally validates every parent reference.
            apply_afisha_delete(data, item)
        if section == "films":
            delete_film(item_id)
            data = storage.load()
        else:
            delete_item_by_id(data[section], item_id)
        if section != "films":
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
        notify_other_user_about_calendar_item=notify_other_user_about_calendar_item,
    )

    if app.job_queue is not None:
        app.job_queue.run_repeating(check_afisha_notifications, interval=NOTIFICATION_CHECK_INTERVAL, first=30, name="afisha_notifications")
    else:
        logger.warning("JobQueue недоступна. Для уведомлений за день до события нужен APScheduler в requirements.")

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|(films|wishlist|leisure|afisha|backlog)$"),
                CallbackQueryHandler(section_router),
            ],
            SECTION: [
                CallbackQueryHandler(noop, pattern=r"^noop$"),
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|(films|wishlist|leisure|afisha|backlog)$"),
                CallbackQueryHandler(section_router),
            ],
            ADDING_FILM_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_film_title)],
            ADDING_FILM_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_film_comment)],
            ADDING_CALENDAR_EVENT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_calendar_event_title)],
            ADDING_CALENDAR_EVENT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_calendar_event_date)],
            ADDING_CALENDAR_EVENT_START_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_calendar_event_start_time)],
            ADDING_CALENDAR_EVENT_END_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_calendar_event_end_time)],
            ADDING_CALENDAR_EVENT_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_calendar_event_comment)],
            EDITING_CALENDAR_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_calendar_date)],
            EDITING_CALENDAR_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_calendar_time)],
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
            EDITING_AFISHA_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_afisha_date)],
            EDITING_AFISHA_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_afisha_time)],
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
