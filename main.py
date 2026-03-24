import json
import os
import uuid
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

DATA_FILE = Path("data.json")

# Заполни usernames без @
ALLOWED_USERS = {
    "wp_bvv": {"name": "Вова", "wishlist_owner": "vova"},
    "privetnormalno": {"name": "Саша", "wishlist_owner": "sasha"},
}

MENU, FILMS, WISHLIST, LEISURE = range(4)
ADDING_FILM_TITLE, ADDING_FILM_COMMENT = range(10, 12)
ADDING_WISHLIST_TITLE, ADDING_WISHLIST_LINK, ADDING_WISHLIST_COMMENT = range(20, 23)
ADDING_LEISURE_TITLE, ADDING_LEISURE_COMMENT = range(30, 32)

FILM_STATUSES = ["want", "watched"]
WISHLIST_STATUSES = ["active", "gifted"]
LEISURE_STATUSES = ["want", "done"]

FILM_STATUS_LABELS = {
    "want": "Хочу посмотреть",
    "watched": "Посмотрели",
}
WISHLIST_STATUS_LABELS = {
    "active": "Актуально",
    "gifted": "Подарено",
}
LEISURE_STATUS_LABELS = {
    "want": "Хотим сделать",
    "done": "Сделано",
}

SECTION_TITLES = {
    "films": "🎬 Фильмы",
    "wishlist": "🎁 Wishlist",
    "leisure": "✨ Досуг",
}

WISHLIST_OWNER_LABELS = {
    "vova": "Вова",
    "sasha": "Саша",
    "unknown": "Без владельца",
}


def default_data() -> dict:
    return {"films": [], "wishlist": [], "leisure": []}


def make_id() -> str:
    return uuid.uuid4().hex[:8]


def normalize_film(item):
    if isinstance(item, str):
        return {
            "id": make_id(),
            "title": item,
            "status": "want",
            "added_by": "unknown",
            "comment": "",
        }
    if isinstance(item, dict):
        return {
            "id": item.get("id", make_id()),
            "title": item.get("title", "Без названия"),
            "status": item.get("status", "want") if item.get("status") in FILM_STATUSES else "want",
            "added_by": item.get("added_by", "unknown"),
            "comment": item.get("comment", ""),
        }
    return None


def normalize_wishlist(item):
    if isinstance(item, str):
        return {
            "id": make_id(),
            "title": item,
            "link": "",
            "comment": "",
            "status": "active",
            "owner": "unknown",
        }
    if isinstance(item, dict):
        owner = item.get("owner", "unknown")
        if owner not in {"vova", "sasha", "unknown"}:
            owner = "unknown"
        return {
            "id": item.get("id", make_id()),
            "title": item.get("title", "Без названия"),
            "link": item.get("link", ""),
            "comment": item.get("comment", ""),
            "status": item.get("status", "active") if item.get("status") in WISHLIST_STATUSES else "active",
            "owner": owner,
        }
    return None


def normalize_leisure(item):
    if isinstance(item, str):
        return {
            "id": make_id(),
            "title": item,
            "comment": "",
            "status": "want",
        }
    if isinstance(item, dict):
        return {
            "id": item.get("id", make_id()),
            "title": item.get("title", "Без названия"),
            "comment": item.get("comment", ""),
            "status": item.get("status", "want") if item.get("status") in LEISURE_STATUSES else "want",
        }
    return None


def load_data() -> dict:
    if not DATA_FILE.exists():
        return default_data()

    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return default_data()

    data = default_data()

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

    return data


def save_data(data: dict) -> None:
    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_username(update: Update) -> str:
    user = update.effective_user
    if not user or not user.username:
        return ""
    return user.username


def get_allowed_profile(update: Update):
    username = get_username(update)
    return ALLOWED_USERS.get(username)


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
    current = get_wishlist_owner_by_user(update)
    return "sasha" if current == "vova" else "vova"


def get_other_wishlist_name(update: Update) -> str:
    current = get_wishlist_owner_by_user(update)

    if current == "vova":
        return "Саша"
    if current == "sasha":
        return "Вова"

    return "Другой"


def wishlist_view_keyboard(update: Update) -> InlineKeyboardMarkup:
    my_owner = get_wishlist_owner_by_user(update)
    my_name = get_user_name(update)
    other_owner = get_other_wishlist_owner(update)
    other_name = get_other_wishlist_name(update)

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"📋 Вишлист: {my_name}", callback_data=f"list_wishlist_owner|{my_owner}")],
            [InlineKeyboardButton(f"📋 Вишлист: {other_name}", callback_data=f"list_wishlist_owner|{other_owner}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="menu_wishlist")],
        ]
    )


def find_item(items: list, item_id: str):
    for item in items:
        if item.get("id") == item_id:
            return item
    return None


def delete_item_by_id(items: list, item_id: str) -> bool:
    for index, item in enumerate(items):
        if item.get("id") == item_id:
            del items[index]
            return True
    return False


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎬 Фильмы", callback_data="menu_films")],
            [InlineKeyboardButton("🎁 Wishlist", callback_data="menu_wishlist")],
            [InlineKeyboardButton("✨ Досуг", callback_data="menu_leisure")],
        ]
    )


def section_keyboard(section: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("➕ Добавить", callback_data=f"add_{section}")]]

    if section == "wishlist":
        rows.append([InlineKeyboardButton("📋 Посмотреть список", callback_data="wishlist_choose_owner")])
    else:
        rows.append([InlineKeyboardButton("📋 Посмотреть список", callback_data=f"list_{section}")])

    rows.append([InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def item_status_label(section: str, status: str) -> str:
    if section == "films":
        return FILM_STATUS_LABELS.get(status, status)
    if section == "wishlist":
        return WISHLIST_STATUS_LABELS.get(status, status)
    if section == "leisure":
        return LEISURE_STATUS_LABELS.get(status, status)
    return status


def build_item_text(section: str, item: dict) -> str:
    if section == "films":
        lines = [
            f"🎬 {item['title']}",
            f"Статус: {item_status_label(section, item['status'])}",
            f"Добавил: {item.get('added_by', 'unknown')}",
        ]
        if item.get("comment"):
            lines.append(f"Комментарий: {item['comment']}")
        return "\n".join(lines)

    if section == "wishlist":
        owner_label = WISHLIST_OWNER_LABELS.get(item.get("owner", "unknown"), "Без владельца")
        lines = [
            f"🎁 {item['title']}",
            f"Чей вишлист: {owner_label}",
            f"Статус: {item_status_label(section, item['status'])}",
        ]
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

    return item.get("title", "Без названия")


def item_keyboard(section: str, item: dict) -> InlineKeyboardMarkup:
    item_id = item["id"]

    if section == "films":
        toggle_to = "watched" if item["status"] == "want" else "want"
        toggle_text = "✅ Отметить как посмотрели" if toggle_to == "watched" else "↩️ Вернуть в хочу посмотреть"
        back_callback = "list_films"
    elif section == "wishlist":
        toggle_to = "gifted" if item["status"] == "active" else "active"
        toggle_text = "🎁 Отметить как подарено" if toggle_to == "gifted" else "↩️ Вернуть в актуальное"
        owner = item.get("owner", "unknown")
        back_callback = f"list_wishlist_owner|{owner}"
    else:
        toggle_to = "done" if item["status"] == "want" else "want"
        toggle_text = "✅ Отметить как сделано" if toggle_to == "done" else "↩️ Вернуть в планы"
        back_callback = "list_leisure"

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(toggle_text, callback_data=f"status|{section}|{item_id}|{toggle_to}")],
            [InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_confirm|{section}|{item_id}")],
            [InlineKeyboardButton("⬅️ К списку", callback_data=back_callback)],
            [InlineKeyboardButton("🏠 В меню", callback_data="back_main")],
        ]
    )


def delete_confirm_keyboard(section: str, item: dict) -> InlineKeyboardMarkup:
    item_id = item["id"]

    if section == "films":
        back_callback = f"view|films|{item_id}"
    elif section == "wishlist":
        back_callback = f"view|wishlist|{item_id}"
    else:
        back_callback = f"view|leisure|{item_id}"

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Да, удалить", callback_data=f"delete|{section}|{item_id}")],
            [InlineKeyboardButton("↩️ Нет, вернуться", callback_data=back_callback)],
        ]
    )


def list_keyboard(section: str, items: list[dict], owner: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    for item in items:
        rows.append([
            InlineKeyboardButton(
                f"{item['title']} · {item_status_label(section, item['status'])}",
                callback_data=f"view|{section}|{item['id']}",
            )
        ])

    if section == "wishlist":
        rows.append([InlineKeyboardButton("➕ Добавить", callback_data="add_wishlist")])
        rows.append([InlineKeyboardButton("⬅️ Выбрать другой вишлист", callback_data="wishlist_choose_owner")])
    else:
        rows.append([InlineKeyboardButton("➕ Добавить", callback_data=f"add_{section}")])
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"menu_{section}")])

    return InlineKeyboardMarkup(rows)


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    username = user.username if user and user.username else "нет username"
    text = f"id: {user.id}\nusername: {username}" if user else "Пользователь не найден"
    await update.message.reply_text(text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    context.user_data.clear()
    name = get_user_name(update)
    text = f"Привет, {name}! Это ваш бот для общих списков.\n\nЧто хочешь открыть?"
    if update.message:
        await update.message.reply_text(text, reply_markup=main_menu_keyboard())
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=main_menu_keyboard())
    return MENU


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()

    mapping = {
        "menu_films": ("🎬 Фильмы", "films", FILMS),
        "menu_wishlist": ("🎁 Wishlist", "wishlist", WISHLIST),
        "menu_leisure": ("✨ Досуг", "leisure", LEISURE),
    }

    title, section_name, state = mapping[query.data]
    await query.edit_message_text(
        f"{title}\n\nВыберите действие:",
        reply_markup=section_keyboard(section_name),
    )
    return state


async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    return await start(update, context)


async def section_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "add_films":
        await query.edit_message_text("Отправь название фильма одним сообщением:")
        return ADDING_FILM_TITLE

    if data == "add_wishlist":
        await query.edit_message_text(
            "Отправь название подарка или пункта wishlist:\n\nОн автоматически попадёт в твой вишлист."
        )
        return ADDING_WISHLIST_TITLE

    if data == "add_leisure":
        await query.edit_message_text("Отправь идею для досуга одним сообщением:")
        return ADDING_LEISURE_TITLE

    if data == "list_films":
        return await show_list(update, context, "films", FILMS)

    if data == "list_leisure":
        return await show_list(update, context, "leisure", LEISURE)

    if data == "wishlist_choose_owner":
        await query.edit_message_text("Чей вишлист открыть?", reply_markup=wishlist_view_keyboard(update))
        return WISHLIST

    if data.startswith("list_wishlist_owner|"):
        owner = data.split("|", 1)[1]
        return await show_list(update, context, "wishlist", WISHLIST, owner=owner)

    return MENU


async def show_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    key: str,
    next_state: int,
    owner: str | None = None,
) -> int:
    query = update.callback_query
    data = load_data()
    items = data.get(key, [])
    title = SECTION_TITLES[key]

    if key == "wishlist" and owner:
        items = [item for item in items if item.get("owner") == owner]
        owner_label = WISHLIST_OWNER_LABELS.get(owner, owner)
        title = f"🎁 Wishlist · {owner_label}"

    if not items:
        if key == "wishlist":
            text = f"{title}\n\nПока пусто."
            keyboard = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("➕ Добавить в мой вишлист", callback_data="add_wishlist")],
                    [InlineKeyboardButton("⬅️ Выбрать другой вишлист", callback_data="wishlist_choose_owner")],
                ]
            )
        else:
            text = f"{title}\n\nПока пусто. Добавьте первый элемент."
            keyboard = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("➕ Добавить", callback_data=f"add_{key}")],
                    [InlineKeyboardButton("⬅️ Назад", callback_data=f"menu_{key}")],
                ]
            )
        await query.edit_message_text(text, reply_markup=keyboard)
        return next_state

    text = f"{title}\n\nНажми на элемент, чтобы открыть карточку, сменить статус или удалить его."
    await query.edit_message_text(text, reply_markup=list_keyboard(key, items, owner=owner))
    return next_state


async def view_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    _, section, item_id = query.data.split("|")

    data = load_data()
    item = find_item(data[section], item_id)
    if not item:
        await query.edit_message_text(
            "Элемент не найден.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ К списку", callback_data=f"list_{section}")]]
            ),
        )
        return {"films": FILMS, "wishlist": WISHLIST, "leisure": LEISURE}[section]

    await query.edit_message_text(build_item_text(section, item), reply_markup=item_keyboard(section, item))
    return {"films": FILMS, "wishlist": WISHLIST, "leisure": LEISURE}[section]


async def update_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer("Статус обновлён")
    _, section, item_id, new_status = query.data.split("|")

    data = load_data()
    item = find_item(data[section], item_id)
    if not item:
        await query.edit_message_text(
            "Не удалось обновить статус: элемент не найден.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ К списку", callback_data=f"list_{section}")]]
            ),
        )
        return {"films": FILMS, "wishlist": WISHLIST, "leisure": LEISURE}[section]

    item["status"] = new_status
    save_data(data)

    await query.edit_message_text(build_item_text(section, item), reply_markup=item_keyboard(section, item))
    return {"films": FILMS, "wishlist": WISHLIST, "leisure": LEISURE}[section]


async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    _, section, item_id = query.data.split("|")

    data = load_data()
    item = find_item(data[section], item_id)
    if not item:
        await query.edit_message_text(
            "Не удалось найти элемент для удаления.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 В меню", callback_data="back_main")]]
            ),
        )
        return {"films": FILMS, "wishlist": WISHLIST, "leisure": LEISURE}[section]

    await query.edit_message_text(
        f"{build_item_text(section, item)}\n\nТочно удалить?",
        reply_markup=delete_confirm_keyboard(section, item),
    )
    return {"films": FILMS, "wishlist": WISHLIST, "leisure": LEISURE}[section]


async def delete_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer("Удалено")
    _, section, item_id = query.data.split("|")

    data = load_data()
    item = find_item(data[section], item_id)
    if not item:
        await query.edit_message_text(
            "Не удалось удалить: элемент не найден.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 В меню", callback_data="back_main")]]
            ),
        )
        return {"films": FILMS, "wishlist": WISHLIST, "leisure": LEISURE}[section]

    owner = item.get("owner") if section == "wishlist" else None
    deleted = delete_item_by_id(data[section], item_id)
    if deleted:
        save_data(data)

    if section == "wishlist" and owner:
        remaining_items = [wishlist_item for wishlist_item in data["wishlist"] if wishlist_item.get("owner") == owner]
        owner_label = WISHLIST_OWNER_LABELS.get(owner, owner)
        title = f"🎁 Wishlist · {owner_label}"

        if remaining_items:
            await query.edit_message_text(
                f"{title}\n\nЭлемент удалён.",
                reply_markup=list_keyboard("wishlist", remaining_items, owner=owner),
            )
        else:
            await query.edit_message_text(
                f"{title}\n\nЭлемент удалён. Список пуст.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("➕ Добавить в мой вишлист", callback_data="add_wishlist")],
                        [InlineKeyboardButton("⬅️ Выбрать другой вишлист", callback_data="wishlist_choose_owner")],
                    ]
                ),
            )
        return WISHLIST

    section_titles_after_delete = {
        "films": "🎬 Фильмы",
        "leisure": "✨ Досуг",
    }
    items = data[section]
    title = section_titles_after_delete.get(section, SECTION_TITLES.get(section, "Список"))

    if items:
        await query.edit_message_text(
            f"{title}\n\nЭлемент удалён.",
            reply_markup=list_keyboard(section, items),
        )
    else:
        await query.edit_message_text(
            f"{title}\n\nЭлемент удалён. Список пуст.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("➕ Добавить", callback_data=f"add_{section}")],
                    [InlineKeyboardButton("⬅️ Назад", callback_data=f"menu_{section}")],
                ]
            ),
        )

    return {"films": FILMS, "wishlist": WISHLIST, "leisure": LEISURE}[section]


async def add_film_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

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

    comment = (update.message.text or "").strip()
    if comment == "-":
        comment = ""

    title = context.user_data.get("film_title", "Без названия")
    data = load_data()
    item = {
        "id": make_id(),
        "title": title,
        "status": "want",
        "added_by": get_user_name(update),
        "comment": comment,
    }
    data["films"].append(item)
    save_data(data)

    context.user_data.pop("film_title", None)

    await update.message.reply_text(
        f"Фильм сохранён:\n\n{build_item_text('films', item)}",
        reply_markup=item_keyboard("films", item),
    )
    return FILMS


async def add_wishlist_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

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

    link = (update.message.text or "").strip()
    if link == "-":
        link = ""

    context.user_data["wishlist_link"] = link
    await update.message.reply_text("Теперь отправь комментарий. Если не нужен, напиши -")
    return ADDING_WISHLIST_COMMENT


async def add_wishlist_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    comment = (update.message.text or "").strip()
    if comment == "-":
        comment = ""

    title = context.user_data.get("wishlist_title", "Без названия")
    link = context.user_data.get("wishlist_link", "")

    data = load_data()
    item = {
        "id": make_id(),
        "title": title,
        "link": link,
        "comment": comment,
        "status": "active",
        "owner": get_wishlist_owner_by_user(update),
    }
    data["wishlist"].append(item)
    save_data(data)

    context.user_data.pop("wishlist_title", None)
    context.user_data.pop("wishlist_link", None)

    await update.message.reply_text(
        f"Пункт wishlist сохранён:\n\n{build_item_text('wishlist', item)}",
        reply_markup=item_keyboard("wishlist", item),
    )
    return WISHLIST


async def add_leisure_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

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

    comment = (update.message.text or "").strip()
    if comment == "-":
        comment = ""

    title = context.user_data.get("leisure_title", "Без названия")

    data = load_data()
    item = {
        "id": make_id(),
        "title": title,
        "comment": comment,
        "status": "want",
    }
    data["leisure"].append(item)
    save_data(data)

    context.user_data.pop("leisure_title", None)

    await update.message.reply_text(
        f"Идея для досуга сохранена:\n\n{build_item_text('leisure', item)}",
        reply_markup=item_keyboard("leisure", item),
    )
    return LEISURE


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text("Окей, возвращаемся в главное меню.", reply_markup=main_menu_keyboard())
    return MENU


def build_app() -> Application:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Переменная окружения BOT_TOKEN не установлена.")

    app = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [
                CallbackQueryHandler(menu_router, pattern=r"^menu_(films|wishlist|leisure)$"),
            ],
            FILMS: [
                CallbackQueryHandler(section_action, pattern=r"^(add_films|list_films)$"),
                CallbackQueryHandler(view_item, pattern=r"^view\|films\|"),
                CallbackQueryHandler(update_status, pattern=r"^status\|films\|"),
                CallbackQueryHandler(delete_confirm, pattern=r"^delete_confirm\|films\|"),
                CallbackQueryHandler(delete_item, pattern=r"^delete\|films\|"),
                CallbackQueryHandler(back_to_main, pattern=r"^back_main$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu_(films|wishlist|leisure)$"),
            ],
            WISHLIST: [
                CallbackQueryHandler(section_action, pattern=r"^(add_wishlist|wishlist_choose_owner|list_wishlist_owner\|.*)$"),
                CallbackQueryHandler(view_item, pattern=r"^view\|wishlist\|"),
                CallbackQueryHandler(update_status, pattern=r"^status\|wishlist\|"),
                CallbackQueryHandler(delete_confirm, pattern=r"^delete_confirm\|wishlist\|"),
                CallbackQueryHandler(delete_item, pattern=r"^delete\|wishlist\|"),
                CallbackQueryHandler(back_to_main, pattern=r"^back_main$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu_(films|wishlist|leisure)$"),
            ],
            LEISURE: [
                CallbackQueryHandler(section_action, pattern=r"^(add_leisure|list_leisure)$"),
                CallbackQueryHandler(view_item, pattern=r"^view\|leisure\|"),
                CallbackQueryHandler(update_status, pattern=r"^status\|leisure\|"),
                CallbackQueryHandler(delete_confirm, pattern=r"^delete_confirm\|leisure\|"),
                CallbackQueryHandler(delete_item, pattern=r"^delete\|leisure\|"),
                CallbackQueryHandler(back_to_main, pattern=r"^back_main$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu_(films|wishlist|leisure)$"),
            ],
            ADDING_FILM_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_film_title)],
            ADDING_FILM_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_film_comment)],
            ADDING_WISHLIST_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wishlist_title)],
            ADDING_WISHLIST_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wishlist_link)],
            ADDING_WISHLIST_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wishlist_comment)],
            ADDING_LEISURE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_leisure_title)],
            ADDING_LEISURE_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_leisure_comment)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(conv_handler)
    return app


if __name__ == "__main__":
    application = build_app()
    application.run_polling()