from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

from bot.handlers.afisha import build_afisha_list_button_text
from bot.utils import (
    get_other_wishlist_owner,
    get_user_name,
    get_wishlist_owner_by_user,
    item_status_label,
    owner_label,
    paginate_items,
)


def main_menu_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🏠 В меню", callback_data="menu:main")


def main_menu_row() -> list[InlineKeyboardButton]:
    return [main_menu_button()]


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

    rows.append(main_menu_row())
    return InlineKeyboardMarkup(rows)


def activity_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎬 Фильмы", callback_data="menu|films")],
            [InlineKeyboardButton("✨ Досуг", callback_data="menu|leisure")],
            [InlineKeyboardButton("📍 В Москве", callback_data="places:moscow")],
            [InlineKeyboardButton("🌍 Города", callback_data="places:cities:0")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Чем займемся", callback_data="activity:menu")],
            [InlineKeyboardButton("🎁 Вишлист", callback_data="menu|wishlist")],
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
            main_menu_row(),
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


def build_pagination_row(
    section: str,
    page: int,
    total_pages: int,
    owner: str | None = None,
    status_filter: str | None = None,
) -> list[InlineKeyboardButton]:
    if total_pages <= 1:
        return []
    row: list[InlineKeyboardButton] = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️", callback_data=build_list_callback(section, page - 1, owner, status_filter)))
    row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton("➡️", callback_data=build_list_callback(section, page + 1, owner, status_filter)))
    return row


def list_keyboard(
    section: str,
    items: list[dict[str, Any]],
    page: int,
    owner: str | None = None,
    status_filter: str | None = None,
) -> InlineKeyboardMarkup:
    page_items, current_page, total_pages = paginate_items(items, page)
    rows: list[list[InlineKeyboardButton]] = []

    for item in page_items:
        if section == "afisha":
            button_text = build_afisha_list_button_text(item)
        else:
            button_text = f"{item['title']} · {item_status_label(section, item['status'])}"
        rows.append(
            [
                InlineKeyboardButton(
                    button_text,
                    callback_data=build_view_callback(section, item["id"], current_page, owner, status_filter),
                )
            ]
        )

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
    rows.append(main_menu_row())

    return InlineKeyboardMarkup(rows)


def build_back_to_list_callback(section: str, page: int, owner: str | None = None, status_filter: str | None = None) -> str:
    return build_list_callback(section, page, owner, status_filter)


def item_keyboard(
    section: str,
    item: dict[str, Any],
    page: int,
    owner: str | None = None,
    status_filter: str | None = None,
) -> InlineKeyboardMarkup:
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
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]
    )


def delete_confirm_keyboard(
    section: str,
    item_id: str,
    page: int,
    owner: str | None = None,
    status_filter: str | None = None,
) -> InlineKeyboardMarkup:
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
            main_menu_row(),
        ]
    )
