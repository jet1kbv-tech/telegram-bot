import json
import logging
import os
import random
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

DATA_FILE = Path("data.json")
PAGE_SIZE = 10
NOTIFY_LOOKAHEAD_MIN = 23 * 60
NOTIFY_LOOKAHEAD_MAX = 25 * 60
NOTIFICATION_CHECK_INTERVAL = 60 * 60

# Заполни usernames без @
ALLOWED_USERS = {
    "wp_bvv": {"name": "Вова", "wishlist_owner": "vova"},
    "privetnormalno": {"name": "Саша", "wishlist_owner": "sasha"},
}

KNOWN_WISHLIST_OWNERS = {"vova", "sasha", "unknown"}

MENU, SECTION = range(2)
(
    ADDING_FILM_TITLE,
    ADDING_FILM_COMMENT,
    ADDING_FILM_RATING,
    ADDING_WISHLIST_TITLE,
    ADDING_WISHLIST_LINK,
    ADDING_WISHLIST_COMMENT,
    ADDING_LEISURE_TITLE,
    ADDING_LEISURE_COMMENT,
    ADDING_EVENT_TITLE,
    ADDING_EVENT_PLACE,
    ADDING_EVENT_DATE,
    ADDING_EVENT_TIME,
    ADDING_EVENT_LINK,
) = range(10, 23)

FILM_STATUSES = ["want", "watched"]
WISHLIST_STATUSES = ["active", "gifted"]
LEISURE_STATUSES = ["want", "done"]
AFISHA_STATUSES = ["active", "done"]

SECTION_CONFIG: dict[str, dict[str, Any]] = {
    "films": {
        "title": "🎬 Фильмы",
        "empty_text": "Пока пусто. Добавьте первый фильм.",
        "statuses": FILM_STATUSES,
        "status_labels": {
            "want": "Непросмотренные",
            "watched": "Просмотренные",
        },
    },
    "wishlist": {
        "title": "🎁 Wishlist",
        "empty_text": "Пока пусто.",
        "statuses": WISHLIST_STATUSES,
        "status_labels": {
            "active": "Актуально",
            "gifted": "Подарено",
        },
    },
    "leisure": {
        "title": "✨ Досуг",
        "empty_text": "Пока пусто. Добавьте первую идею.",
        "statuses": LEISURE_STATUSES,
        "status_labels": {
            "want": "Хотим сделать",
            "done": "Сделано",
        },
    },
    "afisha": {
        "title": "🗓 Афиша",
        "empty_text": "Пока нет актуальных событий.",
        "statuses": AFISHA_STATUSES,
        "status_labels": {
            "active": "Не выполнено",
            "done": "Выполнено",
        },
    },
}

WISHLIST_OWNER_LABELS = {
    "vova": "Вова",
    "sasha": "Саша",
    "unknown": "Без владельца",
}


class JsonStorage:
    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()

    def default_data(self) -> dict[str, Any]:
        return {
            "films": [],
            "wishlist": [],
            "leisure": [],
            "afisha": [],
            "meta": {
                "user_chats": {},
            },
        }

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return self.default_data()

            try:
                with self.path.open("r", encoding="utf-8") as file:
                    raw_data = json.load(file)
            except (json.JSONDecodeError, OSError):
                logger.exception("Не удалось прочитать data.json, использую пустую структуру")
                return self.default_data()

            return self._normalize_data(raw_data)

    def save(self, data: dict[str, Any]) -> None:
        normalized = self._normalize_data(data)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self.path.parent),
                delete=False,
            ) as temp_file:
                json.dump(normalized, temp_file, ensure_ascii=False, indent=2)
                temp_name = temp_file.name
            os.replace(temp_name, self.path)

    def update(self, mutator):
        with self._lock:
            data = self.load()
            result = mutator(data)
            self.save(data)
            return result, data

    def _normalize_data(self, raw_data: Any) -> dict[str, Any]:
        data = self.default_data()
        if not isinstance(raw_data, dict):
            return data

        for raw_item in raw_data.get("films", []):
            item = normalize_film(raw_item)
            if item:
                data["films"].append(item)

        for raw_item in raw_data.get("wishlist", []):
            item = normalize_wishlist(raw_item)
            if item:
                data["wishlist"].append(item)

        for raw_item in raw_data.get("leisure", []):
            item = normalize_leisure(raw_item)
            if item:
                data["leisure"].append(item)

        for raw_item in raw_data.get("afisha", []):
            item = normalize_event(raw_item)
            if item:
                data["afisha"].append(item)

        meta = raw_data.get("meta") if isinstance(raw_data.get("meta"), dict) else {}
        user_chats = meta.get("user_chats") if isinstance(meta.get("user_chats"), dict) else {}
        data["meta"]["user_chats"] = {
            str(username): chat_id
            for username, chat_id in user_chats.items()
            if isinstance(username, str) and isinstance(chat_id, int)
        }
        return data


storage = JsonStorage(DATA_FILE)


def make_id() -> str:
    return uuid.uuid4().hex[:8]


def normalize_rating(value: Any) -> int | None:
    if value in (None, "", "-"):
        return None
    try:
        rating = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= rating <= 10:
        return rating
    return None


def parse_event_dt(item: dict[str, Any]) -> datetime | None:
    date_raw = item.get("date")
    time_raw = item.get("time")
    if not date_raw or not time_raw:
        return None
    try:
        return datetime.strptime(f"{date_raw} {time_raw}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def format_event_dt(item: dict[str, Any]) -> str:
    dt = parse_event_dt(item)
    if not dt:
        return "Дата не указана"
    return dt.strftime("%d.%m.%Y %H:%M")


def is_event_actual(item: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now()
    event_dt = parse_event_dt(item)
    if not event_dt:
        return False
    return item.get("status") == "active" and event_dt >= now


def sort_events(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: parse_event_dt(item) or datetime.max)


def build_calendar_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    now = datetime.now()
    items = [item for item in data.get("afisha", []) if is_event_actual(item, now)]
    return sort_events(items)


def normalize_film(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        return {
            "id": make_id(),
            "title": item,
            "status": "want",
            "added_by": "unknown",
            "comment": "",
            "rating": None,
        }
    if isinstance(item, dict):
        status = item.get("status", "want")
        if status not in FILM_STATUSES:
            status = "want"
        return {
            "id": str(item.get("id") or make_id()),
            "title": str(item.get("title") or "Без названия"),
            "status": status,
            "added_by": str(item.get("added_by") or "unknown"),
            "comment": str(item.get("comment") or ""),
            "rating": normalize_rating(item.get("rating")),
        }
    return None


def normalize_wishlist(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        return {
            "id": make_id(),
            "title": item,
            "link": "",
            "comment": "",
            "status": "active",
            "owner": "unknown",
            "reserved_by": "",
        }
    if isinstance(item, dict):
        status = item.get("status", "active")
        if status not in WISHLIST_STATUSES:
            status = "active"
        owner = item.get("owner", "unknown")
        if owner not in KNOWN_WISHLIST_OWNERS:
            owner = "unknown"
        return {
            "id": str(item.get("id") or make_id()),
            "title": str(item.get("title") or "Без названия"),
            "link": str(item.get("link") or ""),
            "comment": str(item.get("comment") or ""),
            "status": status,
            "owner": owner,
            "reserved_by": str(item.get("reserved_by") or ""),
        }
    return None


def normalize_leisure(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        return {
            "id": make_id(),
            "title": item,
            "comment": "",
            "status": "want",
        }
    if isinstance(item, dict):
        status = item.get("status", "want")
        if status not in LEISURE_STATUSES:
            status = "want"
        return {
            "id": str(item.get("id") or make_id()),
            "title": str(item.get("title") or "Без названия"),
            "comment": str(item.get("comment") or ""),
            "status": status,
        }
    return None


def normalize_event(item: Any) -> dict[str, Any] | None:
    if isinstance(item, dict):
        status = item.get("status", "active")
        if status not in AFISHA_STATUSES:
            status = "active"
        normalized = {
            "id": str(item.get("id") or make_id()),
            "title": str(item.get("title") or "Без названия"),
            "place": str(item.get("place") or ""),
            "date": str(item.get("date") or ""),
            "time": str(item.get("time") or ""),
            "link": str(item.get("link") or ""),
            "status": status,
            "notified_24h": bool(item.get("notified_24h", False)),
        }
        if parse_event_dt(normalized) is None:
            return None
        return normalized
    return None


def get_username(update: Update) -> str:
    user = update.effective_user
    if not user or not user.username:
        return ""
    return user.username


def get_allowed_profile(update: Update) -> dict[str, str] | None:
    return ALLOWED_USERS.get(get_username(update))


async def ensure_access(update: Update) -> bool:
    profile = get_allowed_profile(update)
    if profile:
        return True
    text = (
        "У этого бота закрытый доступ.\n\n"
        "Попроси владельца добавить твой Telegram username в ALLOWED_USERS."
    )
    if update.message:
        await update.message.reply_text(text)
    elif update.callback_query:
        await update.callback_query.answer("Нет доступа", show_alert=True)
    return False


def get_user_name(update: Update) -> str:
    profile = get_allowed_profile(update)
    if profile:
        return profile["name"]
    user = update.effective_user
    if not user:
        return "unknown"
    return user.first_name or user.username or str(user.id)


def get_wishlist_owner_by_user(update: Update) -> str:
    profile = get_allowed_profile(update)
    return profile["wishlist_owner"] if profile else "unknown"


def get_other_wishlist_owner(update: Update) -> str:
    current_owner = get_wishlist_owner_by_user(update)
    if current_owner == "vova":
        return "sasha"
    if current_owner == "sasha":
        return "vova"
    return "unknown"


def owner_label(owner: str) -> str:
    return WISHLIST_OWNER_LABELS.get(owner, owner)


def item_status_label(section: str, status: str) -> str:
    return SECTION_CONFIG[section]["status_labels"].get(status, status)


def find_item(items: list[dict[str, Any]], item_id: str) -> dict[str, Any] | None:
    for item in items:
        if item.get("id") == item_id:
            return item
    return None


def delete_item_by_id(items: list[dict[str, Any]], item_id: str) -> bool:
    for index, item in enumerate(items):
        if item.get("id") == item_id:
            del items[index]
            return True
    return False


def upsert_user_chat_id(data: dict[str, Any], username: str, chat_id: int) -> None:
    if not username or not isinstance(chat_id, int):
        return
    data.setdefault("meta", {}).setdefault("user_chats", {})[username] = chat_id


async def remember_current_chat(update: Update) -> None:
    username = get_username(update)
    chat = update.effective_chat
    if not username or not chat:
        return

    def mutator(data: dict[str, Any]):
        upsert_user_chat_id(data, username, chat.id)
        return None

    storage.update(mutator)


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
            [InlineKeyboardButton("📅 Календарь", callback_data="calendar|0")],
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


def clamp_page(page: int, total_items: int) -> int:
    if total_items <= 0:
        return 0
    last_page = (total_items - 1) // PAGE_SIZE
    return max(0, min(page, last_page))


def paginate_items(items: list[dict[str, Any]], page: int) -> tuple[list[dict[str, Any]], int, int]:
    page = clamp_page(page, len(items))
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    return items[start:end], page, total_pages


def build_list_callback(section: str, page: int, owner: str | None = None, status_filter: str | None = None) -> str:
    if section == "wishlist" and owner:
        return f"list|wishlist|{owner}|{page}"
    if section == "films" and status_filter:
        return f"list|films|{status_filter}|{page}"
    if section == "calendar":
        return f"calendar|{page}"
    return f"list|{section}|{page}"


def build_view_callback(section: str, item_id: str, page: int, owner: str | None = None, status_filter: str | None = None) -> str:
    if section == "wishlist" and owner:
        return f"view|wishlist|{item_id}|{owner}|{page}"
    if section == "films" and status_filter:
        return f"view|films|{item_id}|{status_filter}|{page}"
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
        if item.get("rating") is not None:
            lines.append(f"Рейтинг: {item['rating']}/10")
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

    return "Элемент"


def build_list_text(section: str, items: list[dict[str, Any]], page: int, total_pages: int, owner: str | None = None, status_filter: str | None = None) -> str:
    title = SECTION_CONFIG[section]["title"]
    if section == "wishlist" and owner:
        title = f"🎁 Wishlist · {owner_label(owner)}"
    elif section == "films" and status_filter:
        title = f"🎬 Фильмы · {item_status_label(section, status_filter)}"

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


def build_calendar_text(items: list[dict[str, Any]], page: int, total_pages: int) -> str:
    total_items = len(items)
    if total_items == 0:
        return "📅 Календарь\n\nБлижайших событий пока нет."
    start_num = page * PAGE_SIZE + 1
    end_num = min(total_items, start_num + PAGE_SIZE - 1)
    return (
        "📅 Календарь\n\n"
        f"Ближайшие события {start_num}–{end_num} из {total_items}.\n"
        "Список собран автоматически из Афиши."
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
        rows.append([InlineKeyboardButton("📅 К календарю", callback_data="calendar|0")])
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu|afisha")])
    else:
        rows.append([InlineKeyboardButton("➕ Добавить", callback_data=f"add|{section}")])
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"menu|{section}")])

    return InlineKeyboardMarkup(rows)


def calendar_keyboard(items: list[dict[str, Any]], page: int) -> InlineKeyboardMarkup:
    page_items, current_page, total_pages = paginate_items(items, page)
    rows: list[list[InlineKeyboardButton]] = []
    for item in page_items:
        rows.append([
            InlineKeyboardButton(
                f"{format_event_dt(item)} · {item['title']}",
                callback_data=f"view|afisha|{item['id']}|{current_page}",
            )
        ])

    pagination_row = build_pagination_row("calendar", current_page, total_pages)
    if pagination_row:
        rows.append(pagination_row)

    rows.append([InlineKeyboardButton("🗓 Открыть Афишу", callback_data="menu|afisha")])
    rows.append([InlineKeyboardButton("🏠 В меню", callback_data="main")])
    return InlineKeyboardMarkup(rows)


def build_back_to_list_callback(section: str, page: int, owner: str | None = None, status_filter: str | None = None) -> str:
    return build_list_callback(section, page, owner, status_filter)


def item_keyboard(section: str, item: dict[str, Any], page: int, owner: str | None = None, status_filter: str | None = None) -> InlineKeyboardMarkup:
    item_id = item["id"]

    if section == "films":
        toggle_to = "watched" if item["status"] == "want" else "want"
        toggle_text = "✅ Отметить как посмотрели" if toggle_to == "watched" else "↩️ Вернуть в непросмотренные"
    elif section == "wishlist":
        toggle_to = "gifted" if item["status"] == "active" else "active"
        toggle_text = "🎁 Отметить как подарено" if toggle_to == "gifted" else "↩️ Вернуть в актуальное"
    elif section == "afisha":
        toggle_to = "done" if item["status"] == "active" else "active"
        toggle_text = "✅ Отметить как выполнено" if toggle_to == "done" else "↩️ Вернуть в не выполнено"
    else:
        toggle_to = "done" if item["status"] == "want" else "want"
        toggle_text = "✅ Отметить как сделано" if toggle_to == "done" else "↩️ Вернуть в планы"

    if section == "wishlist" and owner:
        status_callback = f"status|wishlist|{item_id}|{toggle_to}|{owner}|{page}"
        delete_confirm_callback = f"delete_confirm|wishlist|{item_id}|{owner}|{page}"
    elif section == "films" and status_filter:
        status_callback = f"status|films|{item_id}|{toggle_to}|{status_filter}|{page}"
        delete_confirm_callback = f"delete_confirm|films|{item_id}|{status_filter}|{page}"
    else:
        status_callback = f"status|{section}|{item_id}|{toggle_to}|{page}"
        delete_confirm_callback = f"delete_confirm|{section}|{item_id}|{page}"

    if section == "afisha":
        back_callback = "calendar|0" if not is_event_actual(item) else build_back_to_list_callback(section, page)
    else:
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
    elif section == "films" and status_filter:
        delete_callback = f"delete|films|{item_id}|{status_filter}|{page}"
        back_callback = f"view|films|{item_id}|{status_filter}|{page}"
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
    elif section == "afisha":
        items = build_calendar_items(data)

    if section == "afisha":
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
                [InlineKeyboardButton("📅 Календарь", callback_data="calendar|0")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu|afisha")],
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


async def show_calendar(update: Update, page: int = 0) -> int:
    query = update.callback_query
    data = storage.load()
    items = build_calendar_items(data)
    _, current_page, total_pages = paginate_items(items, page)
    await safe_edit_message(query, build_calendar_text(items, current_page, total_pages), reply_markup=calendar_keyboard(items, current_page))
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


async def show_random_film(update: Update) -> int:
    query = update.callback_query
    data = storage.load()
    unwatched = [item for item in data.get("films", []) if item.get("status") == "want"]
    if not unwatched:
        await safe_edit_message(
            query,
            "🎲 Непросмотренных фильмов пока нет.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить фильм", callback_data="add|films")],
                [InlineKeyboardButton("⬅️ Назад к фильмам", callback_data="menu|films")],
            ]),
        )
        return SECTION

    film = random.choice(unwatched)
    await safe_edit_message(
        query,
        "🎲 Случайный выбор из непросмотренных:\n\n" + build_item_text("films", film),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎲 Выбрать ещё", callback_data="random|films")],
            [InlineKeyboardButton("📋 Все непросмотренные", callback_data="list|films|want|0")],
            [InlineKeyboardButton("🏠 В меню", callback_data="main")],
        ]),
    )
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
            text = (
                f"{name}, привет! Ты же не забыл(а), что завтра у вас событие: {event['title']}\n"
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

    if changed:
        storage.save(data)


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    username = user.username if user and user.username else "нет username"
    text = f"id: {user.id}\nusername: {username}" if user else "Пользователь не найден"
    await update.message.reply_text(text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    await remember_current_chat(update)
    context.user_data.clear()
    name = get_user_name(update)
    text = f"Привет, {name}! Это ваш бот для общих списков.\n\nЧто хочешь открыть?"
    if update.message:
        await update.message.reply_text(text, reply_markup=main_menu_keyboard())
    elif update.callback_query:
        await safe_edit_message(update.callback_query, text, reply_markup=main_menu_keyboard())
    return MENU


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    await remember_current_chat(update)
    query = update.callback_query
    await query.answer()

    _, section = query.data.split("|", 1)
    context.user_data["active_section"] = section
    return await show_section_menu(update, section)


async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    await remember_current_chat(update)
    query = update.callback_query
    await query.answer()
    return await start(update, context)


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return SECTION


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

    if action == "calendar":
        page = int(parts[1]) if len(parts) > 1 else 0
        return await show_calendar(update, page)

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

    if action == "list":
        if parts[1] == "wishlist":
            _, _, owner, page_raw = parts
            return await show_list(update, "wishlist", int(page_raw), owner=owner)
        if parts[1] == "films" and len(parts) == 4:
            _, _, status_filter, page_raw = parts
            return await show_list(update, "films", int(page_raw), status_filter=status_filter)
        _, section, page_raw = parts
        return await show_list(update, section, int(page_raw))

    if action == "view":
        if parts[1] == "wishlist":
            _, _, item_id, owner, page_raw = parts
            return await show_item(update, "wishlist", item_id, int(page_raw), owner=owner)
        if parts[1] == "films" and len(parts) == 5:
            _, _, item_id, status_filter, page_raw = parts
            return await show_item(update, "films", item_id, int(page_raw), status_filter=status_filter)
        _, section, item_id, page_raw = parts
        return await show_item(update, section, item_id, int(page_raw))

    if action == "status":
        if parts[1] == "wishlist":
            _, _, item_id, new_status, owner, page_raw = parts
            page = int(page_raw)
            section = "wishlist"
            status_filter = None
        elif parts[1] == "films" and len(parts) == 6:
            _, _, item_id, new_status, status_filter, page_raw = parts
            page = int(page_raw)
            section = "films"
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
        elif parts[1] == "films" and len(parts) == 5:
            _, _, item_id, status_filter, page_raw = parts
            page = int(page_raw)
            section = "films"
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
        elif parts[1] == "films" and len(parts) == 5:
            _, _, item_id, status_filter, page_raw = parts
            requested_page = int(page_raw)
            section = "films"
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


async def add_film_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Название фильма не должно быть пустым. Попробуй ещё раз:")
        return ADDING_FILM_TITLE
    context.user_data["film_title"] = title
    await update.message.reply_text("Теперь отправь комментарий к фильму одним сообщением. Если не нужен, напиши -")
    return ADDING_FILM_COMMENT


async def add_film_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    comment = (update.message.text or "").strip()
    if comment == "-":
        comment = ""
    context.user_data["film_comment"] = comment
    await update.message.reply_text("Теперь отправь рейтинг фильма от 1 до 10. Если без рейтинга — напиши -")
    return ADDING_FILM_RATING


async def add_film_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    raw_rating = (update.message.text or "").strip()
    rating = normalize_rating(raw_rating)
    if raw_rating != "-" and rating is None:
        await update.message.reply_text("Нужно отправить число от 1 до 10 или -. Попробуй ещё раз:")
        return ADDING_FILM_RATING

    item = {
        "id": make_id(),
        "title": context.user_data.get("film_title", "Без названия"),
        "status": "want",
        "added_by": get_user_name(update),
        "comment": context.user_data.get("film_comment", ""),
        "rating": rating,
    }
    data = storage.load()
    data["films"].append(item)
    storage.save(data)

    context.user_data.pop("film_title", None)
    context.user_data.pop("film_comment", None)
    context.user_data["active_section"] = "films"

    await update.message.reply_text(f"Фильм сохранён:\n\n{build_item_text('films', item)}", reply_markup=item_keyboard("films", item, page=0, status_filter="want"))
    return SECTION


async def add_wishlist_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Название не должно быть пустым. Попробуй ещё раз:")
        return ADDING_WISHLIST_TITLE
    context.user_data["wishlist_title"] = title
    await update.message.reply_text("Теперь отправь ссылку. Если ссылки нет, напиши -")
    return ADDING_WISHLIST_LINK


async def add_wishlist_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    link = (update.message.text or "").strip()
    if link == "-":
        link = ""
    context.user_data["wishlist_link"] = link
    await update.message.reply_text("Теперь отправь комментарий. Если не нужен, напиши -")
    return ADDING_WISHLIST_COMMENT


async def add_wishlist_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    comment = (update.message.text or "").strip()
    if comment == "-":
        comment = ""

    item = {
        "id": make_id(),
        "title": context.user_data.get("wishlist_title", "Без названия"),
        "link": context.user_data.get("wishlist_link", ""),
        "comment": comment,
        "status": "active",
        "owner": get_wishlist_owner_by_user(update),
        "reserved_by": "",
    }
    data = storage.load()
    data["wishlist"].append(item)
    storage.save(data)

    context.user_data.pop("wishlist_title", None)
    context.user_data.pop("wishlist_link", None)
    context.user_data["active_section"] = "wishlist"

    await update.message.reply_text(f"Пункт wishlist сохранён:\n\n{build_item_text('wishlist', item)}", reply_markup=item_keyboard("wishlist", item, page=0, owner=item["owner"]))
    await notify_other_user_about_wishlist_item(context, update, item)
    return SECTION


async def add_leisure_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Идея не должна быть пустой. Попробуй ещё раз:")
        return ADDING_LEISURE_TITLE
    context.user_data["leisure_title"] = title
    await update.message.reply_text("Теперь отправь комментарий. Если не нужен, напиши -")
    return ADDING_LEISURE_COMMENT


async def add_leisure_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    comment = (update.message.text or "").strip()
    if comment == "-":
        comment = ""

    item = {
        "id": make_id(),
        "title": context.user_data.get("leisure_title", "Без названия"),
        "comment": comment,
        "status": "want",
    }
    data = storage.load()
    data["leisure"].append(item)
    storage.save(data)

    context.user_data.pop("leisure_title", None)
    context.user_data["active_section"] = "leisure"
    await update.message.reply_text(f"Идея для досуга сохранена:\n\n{build_item_text('leisure', item)}", reply_markup=item_keyboard("leisure", item, page=0))
    return SECTION


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
    await update.message.reply_text("Теперь отправь время в формате ЧЧ:ММ, например 19:30")
    return ADDING_EVENT_TIME


async def add_event_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    time_raw = (update.message.text or "").strip()
    try:
        datetime.strptime(time_raw, "%H:%M")
    except ValueError:
        await update.message.reply_text("Время должно быть в формате ЧЧ:ММ. Попробуй ещё раз:")
        return ADDING_EVENT_TIME
    context.user_data["event_time"] = time_raw
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
        "link": link,
        "status": "active",
        "notified_24h": False,
    }
    if parse_event_dt(item) is None:
        await update.message.reply_text("Не удалось сохранить событие: дата или время некорректны.")
        return SECTION

    data = storage.load()
    data["afisha"].append(item)
    data["afisha"] = sort_events(data["afisha"])
    storage.save(data)

    for key in ["event_title", "event_place", "event_date", "event_time"]:
        context.user_data.pop(key, None)
    context.user_data["active_section"] = "afisha"

    await update.message.reply_text(f"Событие сохранено:\n\n{build_item_text('afisha', item)}", reply_markup=item_keyboard("afisha", item, page=0))
    return SECTION


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    context.user_data.clear()
    await update.message.reply_text("Окей, возвращаемся в главное меню.", reply_markup=main_menu_keyboard())
    return MENU


def build_app() -> Application:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Переменная окружения BOT_TOKEN не установлена.")

    app = Application.builder().token(token).build()

    if app.job_queue is not None:
        app.job_queue.run_repeating(check_afisha_notifications, interval=NOTIFICATION_CHECK_INTERVAL, first=30, name="afisha_notifications")
    else:
        logger.warning("JobQueue недоступна. Для уведомлений за день до события нужен APScheduler в requirements.")

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [
                CallbackQueryHandler(back_to_main, pattern=r"^main$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|(films|wishlist|leisure|afisha)$"),
                CallbackQueryHandler(section_router),
            ],
            SECTION: [
                CallbackQueryHandler(noop, pattern=r"^noop$"),
                CallbackQueryHandler(back_to_main, pattern=r"^main$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|(films|wishlist|leisure|afisha)$"),
                CallbackQueryHandler(section_router),
            ],
            ADDING_FILM_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_film_title)],
            ADDING_FILM_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_film_comment)],
            ADDING_FILM_RATING: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_film_rating)],
            ADDING_WISHLIST_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wishlist_title)],
            ADDING_WISHLIST_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wishlist_link)],
            ADDING_WISHLIST_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wishlist_comment)],
            ADDING_LEISURE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_leisure_title)],
            ADDING_LEISURE_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_leisure_comment)],
            ADDING_EVENT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_title)],
            ADDING_EVENT_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_place)],
            ADDING_EVENT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_date)],
            ADDING_EVENT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_time)],
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
