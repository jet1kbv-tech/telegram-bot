from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.common import main_menu_row
from bot.utils import paginate_items


_BUCKET_TITLES = {
    "active": "🔥 Искра · Активные",
    "done": "🔥 Искра · Завершённые",
}


def spark_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Добавить активность", callback_data="spark:add")],
            [InlineKeyboardButton("📋 Активные", callback_data="spark:list:active:0")],
            [InlineKeyboardButton("✅ Завершённые", callback_data="spark:list:done:0")],
            main_menu_row(),
        ]
    )


def spark_list_keyboard(items: list[dict[str, Any]], bucket: str, page: int) -> InlineKeyboardMarkup:
    page_items, current_page, total_pages = paginate_items(items, page)
    rows: list[list[InlineKeyboardButton]] = []

    for item in page_items:
        rows.append(
            [
                InlineKeyboardButton(
                    item.get("title") or "Без названия",
                    callback_data=f"spark:view:{bucket}:{item['id']}:{current_page}",
                )
            ]
        )

    if total_pages > 1:
        pagination_row: list[InlineKeyboardButton] = []
        if current_page > 0:
            pagination_row.append(InlineKeyboardButton("⬅️", callback_data=f"spark:list:{bucket}:{current_page - 1}"))
        pagination_row.append(InlineKeyboardButton(f"{current_page + 1}/{total_pages}", callback_data="noop"))
        if current_page < total_pages - 1:
            pagination_row.append(InlineKeyboardButton("➡️", callback_data=f"spark:list:{bucket}:{current_page + 1}"))
        rows.append(pagination_row)

    rows.append([InlineKeyboardButton("➕ Добавить активность", callback_data="spark:add")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="spark:menu")])
    rows.append(main_menu_row())
    return InlineKeyboardMarkup(rows)


def spark_item_keyboard(bucket: str, item_id: str, page: int) -> InlineKeyboardMarkup:
    target_bucket = "done" if bucket == "active" else "active"
    toggle_text = "✅ Отметить как выполнено" if target_bucket == "done" else "↩️ Вернуть в активные"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(toggle_text, callback_data=f"spark:toggle:{bucket}:{item_id}:{page}")],
            [InlineKeyboardButton("🗑️ Удалить", callback_data=f"spark:delete_confirm:{bucket}:{item_id}:{page}")],
            [InlineKeyboardButton("⬅️ К списку", callback_data=f"spark:list:{bucket}:{page}")],
            main_menu_row(),
        ]
    )


def spark_delete_confirm_keyboard(bucket: str, item_id: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Да, удалить", callback_data=f"spark:delete:{bucket}:{item_id}:{page}")],
            [InlineKeyboardButton("↩️ Нет, вернуться", callback_data=f"spark:view:{bucket}:{item_id}:{page}")],
            main_menu_row(),
        ]
    )


def spark_bucket_title(bucket: str) -> str:
    return _BUCKET_TITLES.get(bucket, _BUCKET_TITLES["active"])
