from __future__ import annotations

from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.keyboards.places import (
    cities_keyboard,
    city_menu_keyboard,
    city_place_item_keyboard,
    city_places_keyboard,
    moscow_item_keyboard,
    moscow_list_keyboard,
    moscow_menu_keyboard,
    places_menu_keyboard,
)
from bot.states import (
    CITY_ADD_COUNTRY,
    CITY_ADD_NAME,
    CITY_PLACE_ADD_COMMENT,
    CITY_PLACE_ADD_LINK,
    CITY_PLACE_ADD_NAME,
    CITY_PLACE_VISIT_COMMENT,
    PLACE_ADD_COMMENT,
    PLACE_ADD_LINK,
    PLACE_ADD_NAME,
    SECTION,
)
from bot.storage import make_id, storage
from bot.utils import ensure_access, remember_current_chat

_safe_edit_message: Callable[..., Awaitable[None]] | None = None


def configure_places_handlers(*, safe_edit_message: Callable[..., Awaitable[None]]) -> None:
    global _safe_edit_message
    _safe_edit_message = safe_edit_message


def _ensure_places_structure(data: dict) -> dict:
    places = data.setdefault("places", {})
    moscow = places.setdefault("moscow", {})
    moscow.setdefault("active", [])
    moscow.setdefault("visited", [])
    places.setdefault("cities", [])
    return places


def _find_by_id(items: list[dict], item_id: str) -> dict | None:
    for item in items:
        if item.get("id") == item_id:
            return item
    return None


def _place_card_text(place: dict, show_visit_comment: bool = False) -> str:
    lines = [f"📍 {place.get('name', 'Без названия')}"]
    if place.get("yandex_link"):
        lines.append(f"Яндекс Карты: {place['yandex_link']}")
    if place.get("comment"):
        lines.append(f"Комментарий: {place['comment']}")
    if show_visit_comment and place.get("visit_comment"):
        lines.append(f"Комментарий после посещения: {place['visit_comment']}")
    return "\n".join(lines)


async def _edit_or_reply(update: Update, text: str, reply_markup=None) -> None:
    if _safe_edit_message is None:
        raise RuntimeError("Places handlers are not configured")

    if update.callback_query:
        await _safe_edit_message(update.callback_query, text, reply_markup=reply_markup)
    elif update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)


async def show_places_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _edit_or_reply(update, "📍 Места", reply_markup=places_menu_keyboard())
    return SECTION


async def show_moscow_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _edit_or_reply(update, "📍 Локации в Москве", reply_markup=moscow_menu_keyboard())
    return SECTION


async def show_moscow_list(update: Update, status: str, page: int) -> int:
    data = storage.load()
    places = _ensure_places_structure(data)
    status_key = "visited" if status == "visited" else "active"
    items = places["moscow"][status_key]
    title = "✅ Москва · Посетили" if status_key == "visited" else "📍 Москва · Актуальные"
    await _edit_or_reply(update, title, reply_markup=moscow_list_keyboard(items, status_key, page))
    return SECTION


async def show_moscow_item(update: Update, item_id: str, status: str, page: int) -> int:
    data = storage.load()
    places = _ensure_places_structure(data)
    status_key = "visited" if status == "visited" else "active"
    item = _find_by_id(places["moscow"][status_key], item_id)
    if not item:
        await _edit_or_reply(update, "Место не найдено.", reply_markup=moscow_menu_keyboard())
        return SECTION

    await _edit_or_reply(update, _place_card_text(item), reply_markup=moscow_item_keyboard(item_id, status_key, page))
    return SECTION


async def show_cities(update: Update, page: int) -> int:
    data = storage.load()
    places = _ensure_places_structure(data)
    await _edit_or_reply(update, "🌍 Города", reply_markup=cities_keyboard(places["cities"], page))
    return SECTION


async def open_city(update: Update, city_id: str) -> int:
    data = storage.load()
    places = _ensure_places_structure(data)
    city = _find_by_id(places["cities"], city_id)
    if not city:
        await _edit_or_reply(update, "Город не найден.", reply_markup=places_menu_keyboard())
        return SECTION

    title = f"🌍 {city.get('name', 'Без названия')}"
    if city.get("country"):
        title += f"\nСтрана: {city['country']}"
    await _edit_or_reply(update, title, reply_markup=city_menu_keyboard(city_id))
    return SECTION


async def show_city_places(update: Update, city_id: str, status: str, page: int) -> int:
    data = storage.load()
    places = _ensure_places_structure(data)
    city = _find_by_id(places["cities"], city_id)
    if not city:
        await _edit_or_reply(update, "Город не найден.", reply_markup=places_menu_keyboard())
        return SECTION

    status_key = "visited" if status == "visited" else "active"
    city_places = city.get("places", {}).get(status_key, [])
    label = "Посетили" if status_key == "visited" else "Актуальные"
    await _edit_or_reply(
        update,
        f"📍 {city.get('name', 'Без названия')} · {label}",
        reply_markup=city_places_keyboard(city_id, city_places, status_key, page),
    )
    return SECTION


async def show_city_place(update: Update, city_id: str, place_id: str, status: str, page: int) -> int:
    data = storage.load()
    places = _ensure_places_structure(data)
    city = _find_by_id(places["cities"], city_id)
    if not city:
        await _edit_or_reply(update, "Город не найден.", reply_markup=places_menu_keyboard())
        return SECTION

    status_key = "visited" if status == "visited" else "active"
    item = _find_by_id(city.get("places", {}).get(status_key, []), place_id)
    if not item:
        await _edit_or_reply(update, "Место не найдено.", reply_markup=city_menu_keyboard(city_id))
        return SECTION

    await _edit_or_reply(
        update,
        _place_card_text(item, show_visit_comment=True),
        reply_markup=city_place_item_keyboard(city_id, place_id, status_key, page),
    )
    return SECTION


async def places_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")

    if query.data == "places:menu":
        return await show_places_menu(update, context)
    if query.data == "places:moscow":
        return await show_moscow_menu(update, context)
    if query.data == "places:add_moscow":
        await _edit_or_reply(update, "Введите название места (обязательно):")
        return PLACE_ADD_NAME

    if parts[:2] == ["places", "moscow"] and len(parts) == 4:
        return await show_moscow_list(update, parts[2], int(parts[3]))
    if parts[:2] == ["places", "view_moscow"] and len(parts) == 5:
        return await show_moscow_item(update, parts[2], parts[3], int(parts[4]))
    if parts[:2] == ["places", "visit_moscow"] and len(parts) == 4:
        item_id = parts[2]
        page = int(parts[3])

        data = storage.load()
        places = _ensure_places_structure(data)
        active = places["moscow"]["active"]
        item = _find_by_id(active, item_id)
        if item:
            active[:] = [place for place in active if place.get("id") != item_id]
            places["moscow"]["visited"].append(item)
            storage.save(data)

        return await show_moscow_list(update, "active", page)
    if parts[:2] == ["places", "delete_moscow"] and len(parts) == 5:
        item_id = parts[2]
        status = parts[3]
        page = int(parts[4])
        data = storage.load()
        places = _ensure_places_structure(data)
        bucket = places["moscow"]["visited" if status == "visited" else "active"]
        bucket[:] = [item for item in bucket if item.get("id") != item_id]
        storage.save(data)
        return await show_moscow_list(update, status, page)

    if parts[:2] == ["places", "cities"] and len(parts) == 3:
        return await show_cities(update, int(parts[2]))
    if query.data == "places:add_city":
        await _edit_or_reply(update, "Введите название города (обязательно):")
        return CITY_ADD_NAME
    if parts[:2] == ["places", "open_city"] and len(parts) == 3:
        return await open_city(update, parts[2])
    if parts[:2] == ["places", "delete_city"] and len(parts) == 3:
        city_id = parts[2]
        data = storage.load()
        places = _ensure_places_structure(data)
        places["cities"] = [city for city in places["cities"] if city.get("id") != city_id]
        storage.save(data)
        return await show_cities(update, 0)

    if parts[:2] == ["places", "add_city_place"] and len(parts) == 3:
        context.user_data["places_city_id"] = parts[2]
        await _edit_or_reply(update, "Введите название места (обязательно):")
        return CITY_PLACE_ADD_NAME
    if parts[:2] == ["places", "city_active"] and len(parts) == 4:
        return await show_city_places(update, parts[2], "active", int(parts[3]))
    if parts[:2] == ["places", "city_visited"] and len(parts) == 4:
        return await show_city_places(update, parts[2], "visited", int(parts[3]))
    if parts[:2] == ["places", "view_city_place"] and len(parts) == 6:
        return await show_city_place(update, parts[2], parts[3], parts[4], int(parts[5]))
    if parts[:2] == ["places", "delete_city_place"] and len(parts) == 6:
        city_id, place_id, status, page = parts[2], parts[3], parts[4], int(parts[5])
        data = storage.load()
        places = _ensure_places_structure(data)
        city = _find_by_id(places["cities"], city_id)
        if city:
            status_key = "visited" if status == "visited" else "active"
            bucket = city.get("places", {}).get(status_key, [])
            bucket[:] = [item for item in bucket if item.get("id") != place_id]
            storage.save(data)
        return await show_city_places(update, city_id, status, page)
    if parts[:2] == ["places", "visit_city_place"] and len(parts) == 5:
        context.user_data["places_visit_city_id"] = parts[2]
        context.user_data["places_visit_place_id"] = parts[3]
        context.user_data["places_visit_page"] = int(parts[4])
        await _edit_or_reply(update, "Комментарий после посещения (или '-' чтобы пропустить):")
        return CITY_PLACE_VISIT_COMMENT

    return SECTION


async def add_place_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("Название не должно быть пустым. Попробуйте ещё раз:")
        return PLACE_ADD_NAME

    context.user_data["places_name"] = name
    await update.message.reply_text("Ссылка на Яндекс Карты (или '-' чтобы пропустить):")
    return PLACE_ADD_LINK


async def add_place_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    link = (update.message.text or "").strip()
    context.user_data["places_link"] = None if link in {"", "-"} else link
    await update.message.reply_text("Комментарий (или '-' чтобы пропустить):")
    return PLACE_ADD_COMMENT


async def add_place_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    comment_raw = (update.message.text or "").strip()
    comment = None if comment_raw in {"", "-"} else comment_raw

    data = storage.load()
    places = _ensure_places_structure(data)
    places["moscow"]["active"].append(
        {
            "id": make_id(),
            "name": context.user_data.pop("places_name", "Без названия"),
            "yandex_link": context.user_data.pop("places_link", None),
            "comment": comment,
        }
    )
    storage.save(data)

    return await show_moscow_menu(update, context)


async def add_city_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("Название не должно быть пустым. Попробуйте ещё раз:")
        return CITY_ADD_NAME

    context.user_data["places_city_name"] = name
    await update.message.reply_text("Страна (или '-' чтобы пропустить):")
    return CITY_ADD_COUNTRY


async def add_city_country(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    country_raw = (update.message.text or "").strip()
    country = None if country_raw in {"", "-"} else country_raw

    data = storage.load()
    places = _ensure_places_structure(data)
    places["cities"].append(
        {
            "id": make_id(),
            "name": context.user_data.pop("places_city_name", "Без названия"),
            "country": country,
            "places": {
                "active": [],
                "visited": [],
            },
        }
    )
    storage.save(data)

    return await show_cities(update, 0)


async def add_city_place_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("Название не должно быть пустым. Попробуйте ещё раз:")
        return CITY_PLACE_ADD_NAME

    context.user_data["places_city_place_name"] = name
    await update.message.reply_text("Ссылка на Яндекс Карты (или '-' чтобы пропустить):")
    return CITY_PLACE_ADD_LINK


async def add_city_place_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    link = (update.message.text or "").strip()
    context.user_data["places_city_place_link"] = None if link in {"", "-"} else link
    await update.message.reply_text("Комментарий (или '-' чтобы пропустить):")
    return CITY_PLACE_ADD_COMMENT


async def add_city_place_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    city_id = context.user_data.get("places_city_id")
    if not city_id:
        await update.message.reply_text("Не удалось определить город.")
        return SECTION

    comment_raw = (update.message.text or "").strip()
    comment = None if comment_raw in {"", "-"} else comment_raw

    data = storage.load()
    places = _ensure_places_structure(data)
    city = _find_by_id(places["cities"], city_id)
    if not city:
        await update.message.reply_text("Город не найден.")
        return SECTION

    city.setdefault("places", {}).setdefault("active", []).append(
        {
            "id": make_id(),
            "name": context.user_data.pop("places_city_place_name", "Без названия"),
            "yandex_link": context.user_data.pop("places_city_place_link", None),
            "comment": comment,
        }
    )
    storage.save(data)

    return await open_city(update, city_id)


async def add_city_place_visit_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    city_id = context.user_data.pop("places_visit_city_id", None)
    place_id = context.user_data.pop("places_visit_place_id", None)
    page = context.user_data.pop("places_visit_page", 0)

    if not city_id or not place_id:
        await update.message.reply_text("Не удалось найти место для обновления.")
        return SECTION

    comment_raw = (update.message.text or "").strip()
    visit_comment = None if comment_raw in {"", "-"} else comment_raw

    data = storage.load()
    places = _ensure_places_structure(data)
    city = _find_by_id(places["cities"], city_id)
    if not city:
        await update.message.reply_text("Город не найден.")
        return SECTION

    active = city.setdefault("places", {}).setdefault("active", [])
    item = _find_by_id(active, place_id)
    if item:
        active[:] = [place for place in active if place.get("id") != place_id]
        visited_item = {
            "id": item.get("id"),
            "name": item.get("name"),
            "yandex_link": item.get("yandex_link"),
            "comment": item.get("comment"),
            "visit_comment": visit_comment,
        }
        city["places"].setdefault("visited", []).append(visited_item)
        storage.save(data)

    return await show_city_places(update, city_id, "active", page)
