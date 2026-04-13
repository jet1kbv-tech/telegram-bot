from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import PAGE_SIZE
from bot.keyboards.places import main_menu_keyboard, places_menu_keyboard, places_moscow_menu_keyboard
from bot.states import (
    ADDING_CITY_COUNTRY,
    ADDING_CITY_PLACE_COMMENT,
    ADDING_CITY_PLACE_LINK,
    ADDING_CITY_PLACE_TITLE,
    ADDING_CITY_PLACE_VISITED_COMMENT,
    ADDING_CITY_TITLE,
    ADDING_MOSCOW_PLACE_COMMENT,
    ADDING_MOSCOW_PLACE_LINK,
    ADDING_MOSCOW_PLACE_TITLE,
    MENU,
    SECTION,
)
from bot.storage import make_id, storage


def _paginate(items: list[dict[str, Any]], page: int) -> tuple[list[dict[str, Any]], int, int]:
    total = len(items)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    return items[start:start + PAGE_SIZE], page, total_pages


def _find(items: list[dict[str, Any]], item_id: str) -> dict[str, Any] | None:
    for item in items:
        if item.get("id") == item_id:
            return item
    return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = "Привет! Что хочешь открыть?"
    if update.message:
        await update.message.reply_text(text, reply_markup=main_menu_keyboard())
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=main_menu_keyboard())
    return MENU


async def places_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")

    if query.data == "main":
        return await start(update, context)

    if query.data == "places:menu":
        await query.edit_message_text("📍 Места", reply_markup=places_menu_keyboard())
        return SECTION

    if query.data == "places:moscow_menu":
        await query.edit_message_text("📍 Локации в Москве", reply_markup=places_moscow_menu_keyboard())
        return SECTION

    if query.data == "places:moscow_add":
        await query.edit_message_text("Отправь название места в Москве:")
        return ADDING_MOSCOW_PLACE_TITLE

    if parts[0:2] == ["places", "moscow_list"]:
        status, page_raw = parts[2], parts[3]
        page = int(page_raw)
        data = storage.load()
        items = [i for i in data["places"]["moscow"] if i.get("status") == status]
        page_items, current_page, total_pages = _paginate(items, page)
        rows = [[InlineKeyboardButton(i["title"], callback_data=f"places:moscow_view:{i['id']}:{status}:{current_page}")] for i in page_items]
        if total_pages > 1:
            nav = []
            if current_page > 0:
                nav.append(InlineKeyboardButton("⬅️", callback_data=f"places:moscow_list:{status}:{current_page-1}"))
            nav.append(InlineKeyboardButton(f"{current_page+1}/{total_pages}", callback_data="noop"))
            if current_page < total_pages - 1:
                nav.append(InlineKeyboardButton("➡️", callback_data=f"places:moscow_list:{status}:{current_page+1}"))
            rows.append(nav)
        rows.append([InlineKeyboardButton("➕ Добавить", callback_data="places:moscow_add")])
        rows.append([InlineKeyboardButton("⬅️ К Москве", callback_data="places:moscow_menu")])
        rows.append([InlineKeyboardButton("🏠 В меню", callback_data="main")])
        await query.edit_message_text(f"📍 Москва · {'В планах' if status == 'planned' else 'Посещено'}", reply_markup=InlineKeyboardMarkup(rows))
        return SECTION

    if parts[0:2] == ["places", "moscow_view"]:
        item_id, status, page_raw = parts[2], parts[3], parts[4]
        data = storage.load()
        item = _find(data["places"]["moscow"], item_id)
        if not item:
            return await places_callback_router(update, context)
        text = f"📍 {item['title']}\nСтатус: {item['status']}"
        if item.get("link"):
            text += f"\nСсылка: {item['link']}"
        if item.get("comment"):
            text += f"\nКомментарий: {item['comment']}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Отметить посещенным", callback_data=f"places:moscow_visit:{item_id}:{status}:{page_raw}")],
            [InlineKeyboardButton("🗑️ Удалить", callback_data=f"places:moscow_delete:{item_id}:{status}:{page_raw}")],
            [InlineKeyboardButton("⬅️ К списку", callback_data=f"places:moscow_list:{status}:{page_raw}")],
            [InlineKeyboardButton("🏠 В меню", callback_data="main")],
        ])
        await query.edit_message_text(text, reply_markup=kb)
        return SECTION

    if parts[0:2] == ["places", "moscow_visit"]:
        item_id = parts[2]
        data = storage.load()
        item = _find(data["places"]["moscow"], item_id)
        if item:
            item["status"] = "visited"
            storage.save(data)
        await query.edit_message_text("Готово ✅", reply_markup=places_moscow_menu_keyboard())
        return SECTION

    if parts[0:2] == ["places", "moscow_delete"]:
        item_id = parts[2]
        data = storage.load()
        data["places"]["moscow"] = [i for i in data["places"]["moscow"] if i.get("id") != item_id]
        storage.save(data)
        await query.edit_message_text("Локация удалена.", reply_markup=places_moscow_menu_keyboard())
        return SECTION

    if parts[0:2] == ["places", "cities"]:
        page = int(parts[2])
        data = storage.load()
        cities = data["places"]["cities"]
        page_items, current_page, total_pages = _paginate(cities, page)
        rows = [[InlineKeyboardButton(c["title"], callback_data=f"places:city:{c['id']}:0")] for c in page_items]
        if total_pages > 1:
            nav = []
            if current_page > 0:
                nav.append(InlineKeyboardButton("⬅️", callback_data=f"places:cities:{current_page-1}"))
            nav.append(InlineKeyboardButton(f"{current_page+1}/{total_pages}", callback_data="noop"))
            if current_page < total_pages - 1:
                nav.append(InlineKeyboardButton("➡️", callback_data=f"places:cities:{current_page+1}"))
            rows.append(nav)
        rows.append([InlineKeyboardButton("➕ Добавить город", callback_data="places:city_add")])
        rows.append([InlineKeyboardButton("⬅️ К местам", callback_data="places:menu")])
        await query.edit_message_text("🌍 Города", reply_markup=InlineKeyboardMarkup(rows))
        return SECTION

    if query.data == "places:city_add":
        await query.edit_message_text("Отправь название города:")
        return ADDING_CITY_TITLE

    if parts[0:2] == ["places", "city"] and len(parts) >= 3:
        city_id = parts[2]
        data = storage.load()
        city = _find(data["places"]["cities"], city_id)
        if not city:
            await query.edit_message_text("Город не найден.", reply_markup=places_menu_keyboard())
            return SECTION
        context.user_data["active_city_id"] = city_id
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📍 В планах", callback_data=f"places:city_places:{city_id}:planned:0")],
            [InlineKeyboardButton("✅ Посещено", callback_data=f"places:city_places:{city_id}:visited:0")],
            [InlineKeyboardButton("➕ Добавить место", callback_data=f"places:city_place_add:{city_id}")],
            [InlineKeyboardButton("🗑️ Удалить город", callback_data=f"places:city_delete:{city_id}")],
            [InlineKeyboardButton("⬅️ К городам", callback_data="places:cities:0")],
        ])
        await query.edit_message_text(f"🌍 {city['title']}", reply_markup=kb)
        return SECTION

    if parts[0:2] == ["places", "city_delete"]:
        city_id = parts[2]
        data = storage.load()
        data["places"]["cities"] = [c for c in data["places"]["cities"] if c.get("id") != city_id]
        storage.save(data)
        await query.edit_message_text("Город удален.", reply_markup=places_menu_keyboard())
        return SECTION

    if parts[0:2] == ["places", "city_place_add"]:
        context.user_data["active_city_id"] = parts[2]
        await query.edit_message_text("Отправь название места в городе:")
        return ADDING_CITY_PLACE_TITLE

    if parts[0:2] == ["places", "city_places"]:
        city_id, status, page_raw = parts[2], parts[3], parts[4]
        page = int(page_raw)
        data = storage.load()
        city = _find(data["places"]["cities"], city_id)
        if not city:
            await query.edit_message_text("Город не найден.", reply_markup=places_menu_keyboard())
            return SECTION
        places = [p for p in city.get("places", []) if p.get("status") == status]
        page_items, current_page, total_pages = _paginate(places, page)
        rows = [[InlineKeyboardButton(p["title"], callback_data=f"places:city_place_view:{city_id}:{p['id']}:{status}:{current_page}")] for p in page_items]
        if total_pages > 1:
            nav = []
            if current_page > 0:
                nav.append(InlineKeyboardButton("⬅️", callback_data=f"places:city_places:{city_id}:{status}:{current_page-1}"))
            nav.append(InlineKeyboardButton(f"{current_page+1}/{total_pages}", callback_data="noop"))
            if current_page < total_pages - 1:
                nav.append(InlineKeyboardButton("➡️", callback_data=f"places:city_places:{city_id}:{status}:{current_page+1}"))
            rows.append(nav)
        rows.append([InlineKeyboardButton("➕ Добавить место", callback_data=f"places:city_place_add:{city_id}")])
        rows.append([InlineKeyboardButton("⬅️ К городу", callback_data=f"places:city:{city_id}:0")])
        await query.edit_message_text(f"📍 Места · {city['title']} · {status}", reply_markup=InlineKeyboardMarkup(rows))
        return SECTION

    if parts[0:2] == ["places", "city_place_view"]:
        city_id, place_id, status, page_raw = parts[2], parts[3], parts[4], parts[5]
        data = storage.load()
        city = _find(data["places"]["cities"], city_id)
        place = _find(city.get("places", []) if city else [], place_id)
        if not place:
            await query.edit_message_text("Место не найдено.", reply_markup=places_menu_keyboard())
            return SECTION
        text = f"📍 {place['title']}\nСтатус: {place['status']}"
        if place.get("link"):
            text += f"\nСсылка: {place['link']}"
        if place.get("comment"):
            text += f"\nКомментарий: {place['comment']}"
        if place.get("visited_comment"):
            text += f"\nКомментарий после посещения: {place['visited_comment']}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Отметить посещенным", callback_data=f"places:city_place_visit:{city_id}:{place_id}:{status}:{page_raw}")],
            [InlineKeyboardButton("🗑️ Удалить", callback_data=f"places:city_place_delete:{city_id}:{place_id}:{status}:{page_raw}")],
            [InlineKeyboardButton("⬅️ К списку", callback_data=f"places:city_places:{city_id}:{status}:{page_raw}")],
        ])
        await query.edit_message_text(text, reply_markup=kb)
        return SECTION

    if parts[0:2] == ["places", "city_place_visit"]:
        context.user_data["visit_city_id"] = parts[2]
        context.user_data["visit_place_id"] = parts[3]
        await query.edit_message_text("Добавь комментарий о посещении или -")
        return ADDING_CITY_PLACE_VISITED_COMMENT

    if parts[0:2] == ["places", "city_place_delete"]:
        city_id, place_id = parts[2], parts[3]
        data = storage.load()
        city = _find(data["places"]["cities"], city_id)
        if city:
            city["places"] = [p for p in city.get("places", []) if p.get("id") != place_id]
            storage.save(data)
        await query.edit_message_text("Место удалено.", reply_markup=places_menu_keyboard())
        return SECTION

    await query.answer("Неизвестная команда")
    return SECTION


async def add_moscow_place_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Название не должно быть пустым. Попробуй еще раз:")
        return ADDING_MOSCOW_PLACE_TITLE
    context.user_data["moscow_place_title"] = title
    await update.message.reply_text("Ссылка на Яндекс Карты или '-' чтобы пропустить")
    return ADDING_MOSCOW_PLACE_LINK


async def add_moscow_place_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    link = (update.message.text or "").strip()
    context.user_data["moscow_place_link"] = "" if link == "-" else link
    await update.message.reply_text("Комментарий или '-' чтобы пропустить")
    return ADDING_MOSCOW_PLACE_COMMENT


async def add_moscow_place_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    comment = (update.message.text or "").strip()
    data = storage.load()
    data["places"]["moscow"].append({
        "id": make_id(),
        "title": context.user_data.pop("moscow_place_title", "Без названия"),
        "link": context.user_data.pop("moscow_place_link", ""),
        "comment": "" if comment == "-" else comment,
        "status": "planned",
    })
    storage.save(data)
    await update.message.reply_text("Локация сохранена.", reply_markup=places_moscow_menu_keyboard())
    return SECTION


async def add_city_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Название города не должно быть пустым. Попробуй еще раз:")
        return ADDING_CITY_TITLE
    context.user_data["city_title"] = title
    await update.message.reply_text("Страна или '-' чтобы пропустить")
    return ADDING_CITY_COUNTRY


async def add_city_country(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    country = (update.message.text or "").strip()
    data = storage.load()
    data["places"]["cities"].append({
        "id": make_id(),
        "title": context.user_data.pop("city_title", "Без названия"),
        "country": "" if country == "-" else country,
        "places": [],
    })
    storage.save(data)
    await update.message.reply_text("Город сохранен.", reply_markup=places_menu_keyboard())
    return SECTION


async def add_city_place_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Название места не должно быть пустым. Попробуй еще раз:")
        return ADDING_CITY_PLACE_TITLE
    context.user_data["city_place_title"] = title
    await update.message.reply_text("Ссылка на Яндекс Карты или '-' чтобы пропустить")
    return ADDING_CITY_PLACE_LINK


async def add_city_place_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    link = (update.message.text or "").strip()
    context.user_data["city_place_link"] = "" if link == "-" else link
    await update.message.reply_text("Комментарий или '-' чтобы пропустить")
    return ADDING_CITY_PLACE_COMMENT


async def add_city_place_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    city_id = context.user_data.get("active_city_id")
    comment = (update.message.text or "").strip()
    data = storage.load()
    city = _find(data["places"]["cities"], city_id)
    if not city:
        await update.message.reply_text("Город не найден.", reply_markup=main_menu_keyboard())
        return MENU
    city.setdefault("places", []).append({
        "id": make_id(),
        "title": context.user_data.pop("city_place_title", "Без названия"),
        "link": context.user_data.pop("city_place_link", ""),
        "comment": "" if comment == "-" else comment,
        "visited_comment": "",
        "status": "planned",
    })
    storage.save(data)
    await update.message.reply_text("Место сохранено.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К городу", callback_data=f"places:city:{city_id}:0")]]))
    return SECTION


async def add_city_place_visited_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    city_id = context.user_data.pop("visit_city_id", "")
    place_id = context.user_data.pop("visit_place_id", "")
    comment = (update.message.text or "").strip()
    data = storage.load()
    city = _find(data["places"]["cities"], city_id)
    place = _find(city.get("places", []) if city else [], place_id)
    if not place:
        await update.message.reply_text("Место не найдено.", reply_markup=main_menu_keyboard())
        return MENU
    place["status"] = "visited"
    place["visited_comment"] = "" if comment == "-" else comment
    storage.save(data)
    await update.message.reply_text("Отметил как посещенное.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К городу", callback_data=f"places:city:{city_id}:0")]]))
    return SECTION
