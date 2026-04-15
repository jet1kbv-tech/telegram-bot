from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.common import main_menu_row
from bot.utils import paginate_items


def places_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📍 Локации в Москве", callback_data="places:moscow")],
            [InlineKeyboardButton("🌍 Города", callback_data="places:cities:0")],
            main_menu_row(),
        ]
    )


def moscow_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📍 Актуальные", callback_data="places:moscow:active:0")],
            [InlineKeyboardButton("✅ Посетили", callback_data="places:moscow:visited:0")],
            [InlineKeyboardButton("➕ Добавить", callback_data="places:add_moscow")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="places:menu")],
            main_menu_row(),
        ]
    )


def city_menu_keyboard(city_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📍 Актуальные", callback_data=f"places:city_active:{city_id}:0")],
            [InlineKeyboardButton("✅ Посетили", callback_data=f"places:city_visited:{city_id}:0")],
            [InlineKeyboardButton("➕ Добавить место", callback_data=f"places:add_city_place:{city_id}")],
            [InlineKeyboardButton("🗑️ Удалить город", callback_data=f"places:delete_city:{city_id}")],
            [InlineKeyboardButton("⬅️ К городам", callback_data="places:cities:0")],
            main_menu_row(),
        ]
    )


def _pagination_row(prev_callback: str | None, next_callback: str | None, page: int, total_pages: int) -> list[InlineKeyboardButton]:
    row: list[InlineKeyboardButton] = []
    if prev_callback:
        row.append(InlineKeyboardButton("⬅️", callback_data=prev_callback))
    row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if next_callback:
        row.append(InlineKeyboardButton("➡️", callback_data=next_callback))
    return row


def moscow_list_keyboard(items: list[dict[str, Any]], status: str, page: int) -> InlineKeyboardMarkup:
    page_items, current_page, total_pages = paginate_items(items, page)
    rows: list[list[InlineKeyboardButton]] = []

    for item in page_items:
        rows.append([InlineKeyboardButton(item["name"], callback_data=f"places:view_moscow:{item['id']}:{status}:{current_page}")])

    if total_pages > 1:
        prev_callback = f"places:moscow:{status}:{current_page - 1}" if current_page > 0 else None
        next_callback = f"places:moscow:{status}:{current_page + 1}" if current_page < total_pages - 1 else None
        rows.append(_pagination_row(prev_callback, next_callback, current_page, total_pages))

    rows.append([InlineKeyboardButton("➕ Добавить", callback_data="places:add_moscow")])
    rows.append([InlineKeyboardButton("⬅️ К Москве", callback_data="places:moscow")])
    rows.append(main_menu_row())
    return InlineKeyboardMarkup(rows)


def moscow_item_keyboard(item_id: str, status: str, page: int) -> InlineKeyboardMarkup:
    rows = []
    if status == "active":
        rows.append([InlineKeyboardButton("✅ Посетили", callback_data=f"places:visit_moscow:{item_id}:{page}")])
    rows.extend(
        [
            [InlineKeyboardButton("🗑️ Удалить", callback_data=f"places:delete_moscow:{item_id}:{status}:{page}")],
            [InlineKeyboardButton("⬅️ К списку", callback_data=f"places:moscow:{status}:{page}")],
            main_menu_row(),
        ]
    )
    return InlineKeyboardMarkup(rows)


def cities_keyboard(cities: list[dict[str, Any]], page: int) -> InlineKeyboardMarkup:
    page_items, current_page, total_pages = paginate_items(cities, page)
    rows: list[list[InlineKeyboardButton]] = []

    for city in page_items:
        rows.append([InlineKeyboardButton(city["name"], callback_data=f"places:open_city:{city['id']}")])

    if total_pages > 1:
        prev_callback = f"places:cities:{current_page - 1}" if current_page > 0 else None
        next_callback = f"places:cities:{current_page + 1}" if current_page < total_pages - 1 else None
        rows.append(_pagination_row(prev_callback, next_callback, current_page, total_pages))

    rows.append([InlineKeyboardButton("➕ Добавить город", callback_data="places:add_city")])
    rows.append([InlineKeyboardButton("⬅️ К местам", callback_data="places:menu")])
    rows.append(main_menu_row())
    return InlineKeyboardMarkup(rows)


def city_places_keyboard(city_id: str, items: list[dict[str, Any]], status: str, page: int) -> InlineKeyboardMarkup:
    page_items, current_page, total_pages = paginate_items(items, page)
    rows: list[list[InlineKeyboardButton]] = []

    for item in page_items:
        rows.append([InlineKeyboardButton(item["name"], callback_data=f"places:view_city_place:{city_id}:{item['id']}:{status}:{current_page}")])

    if total_pages > 1:
        prev_callback = f"places:city_{status}:{city_id}:{current_page - 1}" if current_page > 0 else None
        next_callback = f"places:city_{status}:{city_id}:{current_page + 1}" if current_page < total_pages - 1 else None
        rows.append(_pagination_row(prev_callback, next_callback, current_page, total_pages))

    rows.append([InlineKeyboardButton("➕ Добавить место", callback_data=f"places:add_city_place:{city_id}")])
    rows.append([InlineKeyboardButton("⬅️ К городу", callback_data=f"places:open_city:{city_id}")])
    rows.append(main_menu_row())
    return InlineKeyboardMarkup(rows)


def city_place_item_keyboard(city_id: str, place_id: str, status: str, page: int) -> InlineKeyboardMarkup:
    rows = []
    if status == "active":
        rows.append([InlineKeyboardButton("✅ Посетили", callback_data=f"places:visit_city_place:{city_id}:{place_id}:{page}")])
    rows.extend(
        [
            [InlineKeyboardButton("🗑️ Удалить", callback_data=f"places:delete_city_place:{city_id}:{place_id}:{status}:{page}")],
            [InlineKeyboardButton("⬅️ К списку", callback_data=f"places:city_{status}:{city_id}:{page}")],
            main_menu_row(),
        ]
    )
    return InlineKeyboardMarkup(rows)
