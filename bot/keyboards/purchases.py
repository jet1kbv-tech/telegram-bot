from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils import paginate_items

PURCHASE_PLANNED = "planned"
PURCHASE_BOUGHT = "bought"


def purchases_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Добавить покупку", callback_data="purchases:add")],
            [InlineKeyboardButton("📋 В планах", callback_data="purchases:list:planned:0")],
            [InlineKeyboardButton("✅ Куплено", callback_data="purchases:list:bought:0")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]
    )


def purchases_priority_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔥 Высокий", callback_data="purchases:add_priority:high")],
            [InlineKeyboardButton("⚖️ Средний", callback_data="purchases:add_priority:medium")],
            [InlineKeyboardButton("💤 Низкий", callback_data="purchases:add_priority:low")],
            [InlineKeyboardButton("Пропустить", callback_data="purchases:add_priority:skip")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]
    )


def purchases_edit_priority_keyboard(bucket: str, item_id: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔥 Высокий", callback_data=f"purchases:set_priority:{bucket}:{item_id}:high:{page}")],
            [InlineKeyboardButton("⚖️ Средний", callback_data=f"purchases:set_priority:{bucket}:{item_id}:medium:{page}")],
            [InlineKeyboardButton("💤 Низкий", callback_data=f"purchases:set_priority:{bucket}:{item_id}:low:{page}")],
            [InlineKeyboardButton("Пропустить", callback_data=f"purchases:set_priority:{bucket}:{item_id}:skip:{page}")],
            [InlineKeyboardButton("⬅️ К карточке", callback_data=f"purchases:view:{bucket}:{item_id}:{page}")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]
    )


def purchases_list_keyboard(items: list[dict], bucket: str, page: int) -> InlineKeyboardMarkup:
    page_items, current_page, total_pages = paginate_items(items, page)
    rows: list[list[InlineKeyboardButton]] = []

    for item in page_items:
        title = str(item.get("title") or "Без названия")
        price = item.get("price")
        if isinstance(price, int):
            title = f"{title} · {price:,} ₽".replace(",", " ")
        rows.append([InlineKeyboardButton(title, callback_data=f"purchases:view:{bucket}:{item.get('id')}:{current_page}")])

    pagination_row: list[InlineKeyboardButton] = []
    if total_pages > 1:
        if current_page > 0:
            pagination_row.append(InlineKeyboardButton("⬅️", callback_data=f"purchases:list:{bucket}:{current_page - 1}"))
        pagination_row.append(InlineKeyboardButton(f"{current_page + 1}/{total_pages}", callback_data="noop"))
        if current_page < total_pages - 1:
            pagination_row.append(InlineKeyboardButton("➡️", callback_data=f"purchases:list:{bucket}:{current_page + 1}"))
    if pagination_row:
        rows.append(pagination_row)

    rows.append([InlineKeyboardButton("➕ Добавить покупку", callback_data="purchases:add")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="purchases:menu")])
    rows.append([InlineKeyboardButton("🏠 В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def purchases_card_keyboard(bucket: str, item_id: str, page: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("✏️ Изменить название", callback_data=f"purchases:edit:{bucket}:{item_id}:title:{page}")],
        [InlineKeyboardButton("🔗 Изменить ссылку", callback_data=f"purchases:edit:{bucket}:{item_id}:link:{page}")],
        [InlineKeyboardButton("💰 Изменить стоимость", callback_data=f"purchases:edit:{bucket}:{item_id}:price:{page}")],
        [InlineKeyboardButton("🔥 Изменить приоритет", callback_data=f"purchases:edit:{bucket}:{item_id}:priority:{page}")],
        [InlineKeyboardButton("📝 Изменить комментарий", callback_data=f"purchases:edit:{bucket}:{item_id}:comment:{page}")],
        [InlineKeyboardButton("🛍 Куплю я", callback_data=f"purchases:buyer:{bucket}:{item_id}:{page}")],
    ]
    if bucket == PURCHASE_PLANNED:
        rows.append([InlineKeyboardButton("✅ Куплено", callback_data=f"purchases:move:{bucket}:{item_id}:bought:{page}")])
    else:
        rows.append([InlineKeyboardButton("↩️ Вернуть в планы", callback_data=f"purchases:move:{bucket}:{item_id}:planned:{page}")])
    rows.extend(
        [
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"purchases:delete_confirm:{bucket}:{item_id}:{page}")],
            [InlineKeyboardButton("⬅️ К списку", callback_data=f"purchases:list:{bucket}:{page}")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def purchases_delete_confirm_keyboard(bucket: str, item_id: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🗑 Да, удалить", callback_data=f"purchases:delete:{bucket}:{item_id}:{page}")],
            [InlineKeyboardButton("Отмена", callback_data=f"purchases:view:{bucket}:{item_id}:{page}")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]
    )
