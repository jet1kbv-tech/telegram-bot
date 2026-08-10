from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import PAGE_SIZE
from bot.keyboards.purchases import (
    PURCHASE_BOUGHT,
    PURCHASE_PLANNED,
    purchases_card_keyboard,
    purchases_delete_confirm_keyboard,
    purchases_edit_priority_keyboard,
    purchases_list_keyboard,
    purchases_menu_keyboard,
    purchases_priority_keyboard,
)
from bot.states import (
    ADDING_PURCHASE_COMMENT,
    ADDING_PURCHASE_LINK,
    ADDING_PURCHASE_PRICE,
    ADDING_PURCHASE_PRIORITY,
    ADDING_PURCHASE_TITLE,
    EDITING_PURCHASE_FIELD,
    SECTION,
)
from bot.storage import find_item, normalize_purchase_item, storage
from bot.services.actions.purchases import create_purchase
from bot.utils import clamp_page, ensure_access, get_user_name, normalize_entity_title, paginate_items, remember_current_chat

_safe_edit_message: Callable[..., Awaitable[None]] | None = None

PURCHASE_BUCKETS = {PURCHASE_PLANNED, PURCHASE_BOUGHT}
PURCHASE_PRIORITIES = {"high", "medium", "low", ""}
PRIORITY_LABELS = {
    "high": "🔥 Высокий",
    "medium": "⚖️ Средний",
    "low": "💤 Низкий",
}
FIELD_PROMPTS = {
    "title": "Отправь новое название покупки:",
    "link": "Отправь новую ссылку. Если ссылки нет — отправь -",
    "price": "Отправь новую стоимость, например 12000 или 12 000 руб. Если стоимости нет — отправь -",
    "comment": "Отправь новый комментарий. Если комментария нет — отправь -",
}
EDIT_CONTEXT_KEYS = (
    "purchase_edit_bucket",
    "purchase_edit_item_id",
    "purchase_edit_field",
    "purchase_edit_page",
)
ADD_CONTEXT_KEYS = (
    "purchase_title",
    "purchase_link",
    "purchase_price",
    "purchase_priority",
)


def configure_purchases_handlers(*, safe_edit_message: Callable[..., Awaitable[None]]) -> None:
    global _safe_edit_message
    _safe_edit_message = safe_edit_message


def _safe_edit() -> Callable[..., Awaitable[None]]:
    if _safe_edit_message is None:
        raise RuntimeError("Purchases handlers are not configured")
    return _safe_edit_message


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize_bucket(bucket: str) -> str:
    return bucket if bucket in PURCHASE_BUCKETS else PURCHASE_PLANNED


def _parse_page(value: str) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _ensure_purchases(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    purchases = data.setdefault("purchases", {})
    if not isinstance(purchases, dict):
        purchases = {PURCHASE_PLANNED: [], PURCHASE_BOUGHT: []}
        data["purchases"] = purchases
    for bucket in PURCHASE_BUCKETS:
        if not isinstance(purchases.get(bucket), list):
            purchases[bucket] = []
    return purchases


def _bucket_items(data: dict[str, Any], bucket: str) -> list[dict[str, Any]]:
    purchases = _ensure_purchases(data)
    return purchases[_normalize_bucket(bucket)]


def _find_purchase(data: dict[str, Any], bucket: str, item_id: str) -> dict[str, Any] | None:
    return find_item(_bucket_items(data, bucket), item_id)


def parse_purchase_price(raw: str) -> int | None:
    text = (raw or "").strip().lower()
    if text in {"", "-"}:
        return None
    for token in ("руб.", "руб", "₽", "р.", "р"):
        text = text.replace(token, "")
    text = text.replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
    if not text.isdigit():
        raise ValueError("invalid purchase price")
    return int(text)


def _format_price(price: int) -> str:
    return f"{price:,} ₽".replace(",", " ")


def _format_total(items: list[dict[str, Any]]) -> str:
    total = sum(item.get("price") for item in items if isinstance(item.get("price"), int))
    return _format_price(total)


def _bucket_title(bucket: str) -> str:
    return "✅ Куплено" if bucket == PURCHASE_BOUGHT else "📋 В планах"


def _status_label(bucket: str) -> str:
    return "Куплено" if bucket == PURCHASE_BOUGHT else "В планах"


def _priority_label(priority: str) -> str:
    return PRIORITY_LABELS.get(priority, priority)


def _purchase_card_text(item: dict[str, Any], bucket: str) -> str:
    lines = [
        f"🛒 {item.get('title') or 'Без названия'}",
        f"Статус: {_status_label(bucket)}",
    ]
    price = item.get("price")
    if isinstance(price, int):
        lines.append(f"Стоимость: {_format_price(price)}")
    priority = item.get("priority") or ""
    if priority:
        lines.append(f"Приоритет: {_priority_label(priority)}")
    if item.get("link"):
        lines.append(f"Ссылка: {item['link']}")
    if item.get("comment"):
        lines.append(f"Комментарий: {item['comment']}")
    if item.get("buyer"):
        lines.append(f"Покупатель: {item['buyer']}")
    return "\n".join(lines)


def _purchase_list_text(items: list[dict[str, Any]], bucket: str, page: int, total_pages: int) -> str:
    title = _bucket_title(bucket)
    if not items:
        return f"{title}\n\nИтого: {_format_total(items)}\n\nСписок пуст."

    start_num = page * PAGE_SIZE + 1
    end_num = min(len(items), start_num + PAGE_SIZE - 1)
    return (
        f"{title}\n\n"
        f"Итого: {_format_total(items)}\n"
        f"Элементы {start_num}–{end_num} из {len(items)}. Страница {page + 1}/{total_pages}.\n"
        "Нажми на покупку, чтобы открыть карточку."
    )


def _clear_add_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in ADD_CONTEXT_KEYS:
        context.user_data.pop(key, None)


def _clear_edit_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in EDIT_CONTEXT_KEYS:
        context.user_data.pop(key, None)


async def show_purchases_menu(update: Update) -> int:
    query = update.callback_query
    await _safe_edit()(query, "🛒 Покупки\n\nВыберите действие:", reply_markup=purchases_menu_keyboard())
    return SECTION


async def show_purchases_list(update: Update, bucket: str, page: int = 0) -> int:
    query = update.callback_query
    bucket = _normalize_bucket(bucket)
    data = storage.load()
    items = _bucket_items(data, bucket)
    _, current_page, total_pages = paginate_items(items, page)
    await _safe_edit()(
        query,
        _purchase_list_text(items, bucket, current_page, total_pages),
        reply_markup=purchases_list_keyboard(items, bucket, current_page),
    )
    return SECTION


async def show_purchases_item(update: Update, bucket: str, item_id: str, page: int = 0) -> int:
    query = update.callback_query
    bucket = _normalize_bucket(bucket)
    data = storage.load()
    item = _find_purchase(data, bucket, item_id)
    if not item:
        await _safe_edit()(
            query,
            "Покупка не найдена.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К списку", callback_data=f"purchases:list:{bucket}:{page}")]]),
        )
        return SECTION

    await _safe_edit()(query, _purchase_card_text(item, bucket), reply_markup=purchases_card_keyboard(bucket, item_id, page))
    return SECTION


async def add_purchase_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    title = (update.message.text or "").strip()
    if not title or title == "-":
        await update.message.reply_text("Название покупки не должно быть пустым. Попробуй ещё раз:")
        return ADDING_PURCHASE_TITLE
    context.user_data["purchase_title"] = normalize_entity_title(title)
    await update.message.reply_text("Отправь ссылку. Если ссылки нет — отправь -")
    return ADDING_PURCHASE_LINK


async def add_purchase_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    link = (update.message.text or "").strip()
    context.user_data["purchase_link"] = "" if link == "-" else link
    await update.message.reply_text("Отправь стоимость, например 12000 или 12 000 руб. Если стоимости нет — отправь -")
    return ADDING_PURCHASE_PRICE


async def add_purchase_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    try:
        price = parse_purchase_price(update.message.text or "")
    except ValueError:
        await update.message.reply_text(
            "Не смог распознать стоимость. Напиши число, например 12000 или 12 000 руб. Если стоимости нет — отправь -"
        )
        return ADDING_PURCHASE_PRICE
    context.user_data["purchase_price"] = price
    await update.message.reply_text(
        "Выбери приоритет или пропусти:",
        reply_markup=purchases_priority_keyboard(),
    )
    return ADDING_PURCHASE_PRIORITY


async def add_purchase_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)
    comment = (update.message.text or "").strip()
    if comment == "-":
        comment = ""

    item = {
        "title": context.user_data.get("purchase_title", "Без названия"),
        "link": context.user_data.get("purchase_link", ""),
        "price": context.user_data.get("purchase_price"),
        "priority": context.user_data.get("purchase_priority", ""),
        "comment": comment,
        "buyer": "",
    }
    try:
        normalized_item = create_purchase(item)
    except ValueError:
        await update.message.reply_text("Не удалось сохранить покупку.")
        _clear_add_context(context)
        return SECTION

    _clear_add_context(context)
    await update.message.reply_text(
        f"Покупка добавлена:\n\n{_purchase_card_text(normalized_item, PURCHASE_PLANNED)}",
        reply_markup=purchases_card_keyboard(PURCHASE_PLANNED, normalized_item["id"], 0),
    )
    return SECTION


async def edit_purchase_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    bucket = _normalize_bucket(str(context.user_data.get("purchase_edit_bucket") or PURCHASE_PLANNED))
    item_id = str(context.user_data.get("purchase_edit_item_id") or "")
    field = str(context.user_data.get("purchase_edit_field") or "")
    page = _parse_page(str(context.user_data.get("purchase_edit_page") or 0))
    raw_value = (update.message.text or "").strip()

    if field not in {"title", "link", "price", "comment"} or not item_id:
        _clear_edit_context(context)
        await update.message.reply_text("Не удалось понять, что нужно изменить. Открой карточку покупки ещё раз.")
        return SECTION

    if field == "title" and (not raw_value or raw_value == "-"):
        await update.message.reply_text("Название покупки не должно быть пустым. Попробуй ещё раз:")
        return EDITING_PURCHASE_FIELD

    if field == "price":
        try:
            new_value: Any = parse_purchase_price(raw_value)
        except ValueError:
            await update.message.reply_text(
                "Не смог распознать стоимость. Напиши число, например 12000 или 12 000 руб. Если стоимости нет — отправь -"
            )
            return EDITING_PURCHASE_FIELD
    elif field in {"link", "comment"}:
        new_value = "" if raw_value == "-" else raw_value
    else:
        new_value = normalize_entity_title(raw_value)

    updated_item: dict[str, Any] | None = None

    def mutator(data: dict[str, Any]) -> None:
        nonlocal updated_item
        item = _find_purchase(data, bucket, item_id)
        if not item:
            return
        item[field] = new_value
        normalized = normalize_purchase_item(item)
        if normalized:
            item.clear()
            item.update(normalized)
            updated_item = item.copy()

    storage.update(mutator)
    _clear_edit_context(context)

    if not updated_item:
        await update.message.reply_text(
            "Покупка не найдена.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К списку", callback_data=f"purchases:list:{bucket}:{page}")]]),
        )
        return SECTION

    await update.message.reply_text(
        f"Покупка обновлена:\n\n{_purchase_card_text(updated_item, bucket)}",
        reply_markup=purchases_card_keyboard(bucket, item_id, page),
    )
    return SECTION


async def purchases_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    if not parts or parts[0] != "purchases":
        return SECTION

    action = parts[1] if len(parts) > 1 else ""

    if action == "menu":
        return await show_purchases_menu(update)

    if action == "add":
        _clear_add_context(context)
        await _safe_edit()(query, "Отправь название покупки:")
        return ADDING_PURCHASE_TITLE

    if action == "add_priority" and len(parts) >= 3:
        priority = parts[2]
        context.user_data["purchase_priority"] = "" if priority == "skip" else priority if priority in PURCHASE_PRIORITIES else ""
        await _safe_edit()(query, "Отправь комментарий. Если комментария нет — отправь -")
        return ADDING_PURCHASE_COMMENT

    if action == "list" and len(parts) >= 4:
        return await show_purchases_list(update, parts[2], _parse_page(parts[3]))

    if action == "view" and len(parts) >= 5:
        return await show_purchases_item(update, parts[2], parts[3], _parse_page(parts[4]))

    if action == "edit" and len(parts) >= 6:
        bucket = _normalize_bucket(parts[2])
        item_id = parts[3]
        field = parts[4]
        page = _parse_page(parts[5])
        if field == "priority":
            await _safe_edit()(
                query,
                "Выбери новый приоритет или пропусти:",
                reply_markup=purchases_edit_priority_keyboard(bucket, item_id, page),
            )
            return SECTION
        prompt = FIELD_PROMPTS.get(field)
        if not prompt:
            return SECTION
        context.user_data["purchase_edit_bucket"] = bucket
        context.user_data["purchase_edit_item_id"] = item_id
        context.user_data["purchase_edit_field"] = field
        context.user_data["purchase_edit_page"] = page
        await _safe_edit()(query, prompt)
        return EDITING_PURCHASE_FIELD

    if action == "set_priority" and len(parts) >= 6:
        bucket = _normalize_bucket(parts[2])
        item_id = parts[3]
        priority = parts[4]
        page = _parse_page(parts[5])
        new_priority = "" if priority == "skip" else priority if priority in PURCHASE_PRIORITIES else ""
        updated = False

        def mutator(data: dict[str, Any]) -> None:
            nonlocal updated
            item = _find_purchase(data, bucket, item_id)
            if not item:
                return
            item["priority"] = new_priority
            updated = True

        storage.update(mutator)
        if not updated:
            await _safe_edit()(query, "Покупка не найдена.", reply_markup=purchases_menu_keyboard())
            return SECTION
        return await show_purchases_item(update, bucket, item_id, page)

    if action == "buyer" and len(parts) >= 5:
        bucket = _normalize_bucket(parts[2])
        item_id = parts[3]
        page = _parse_page(parts[4])
        updated = False

        def mutator(data: dict[str, Any]) -> None:
            nonlocal updated
            item = _find_purchase(data, bucket, item_id)
            if not item:
                return
            item["buyer"] = get_user_name(update)
            updated = True

        storage.update(mutator)
        if not updated:
            await _safe_edit()(query, "Покупка не найдена.", reply_markup=purchases_menu_keyboard())
            return SECTION
        return await show_purchases_item(update, bucket, item_id, page)

    if action == "move" and len(parts) >= 6:
        source_bucket = _normalize_bucket(parts[2])
        item_id = parts[3]
        target_bucket = _normalize_bucket(parts[4])
        page = _parse_page(parts[5])
        moved = False

        def mutator(data: dict[str, Any]) -> None:
            nonlocal moved
            source_items = _bucket_items(data, source_bucket)
            item = find_item(source_items, item_id)
            if not item or source_bucket == target_bucket:
                return
            source_items[:] = [entry for entry in source_items if entry.get("id") != item_id]
            if target_bucket == PURCHASE_BOUGHT:
                item["bought_at"] = _now_iso()
            else:
                item["bought_at"] = ""
            _bucket_items(data, target_bucket).append(item)
            moved = True

        storage.update(mutator)
        if not moved:
            await _safe_edit()(query, "Покупка не найдена.", reply_markup=purchases_menu_keyboard())
            return SECTION
        return await show_purchases_item(update, target_bucket, item_id, 0)

    if action == "delete_confirm" and len(parts) >= 5:
        bucket = _normalize_bucket(parts[2])
        item_id = parts[3]
        page = _parse_page(parts[4])
        data = storage.load()
        item = _find_purchase(data, bucket, item_id)
        if not item:
            await _safe_edit()(query, "Покупка не найдена.", reply_markup=purchases_menu_keyboard())
            return SECTION
        await _safe_edit()(
            query,
            f"{_purchase_card_text(item, bucket)}\n\nТочно удалить?",
            reply_markup=purchases_delete_confirm_keyboard(bucket, item_id, page),
        )
        return SECTION

    if action == "delete" and len(parts) >= 5:
        bucket = _normalize_bucket(parts[2])
        item_id = parts[3]
        requested_page = _parse_page(parts[4])
        deleted = False
        remaining_count = 0

        def mutator(data: dict[str, Any]) -> None:
            nonlocal deleted, remaining_count
            items = _bucket_items(data, bucket)
            before = len(items)
            items[:] = [entry for entry in items if entry.get("id") != item_id]
            deleted = len(items) != before
            remaining_count = len(items)

        storage.update(mutator)
        if not deleted:
            await _safe_edit()(query, "Покупка не найдена.", reply_markup=purchases_menu_keyboard())
            return SECTION
        page = clamp_page(requested_page, remaining_count)
        return await show_purchases_list(update, bucket, page)

    return SECTION
