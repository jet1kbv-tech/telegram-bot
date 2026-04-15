from __future__ import annotations

from typing import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.keyboards.spark import (
    spark_bucket_title,
    spark_delete_confirm_keyboard,
    spark_item_keyboard,
    spark_list_keyboard,
    spark_menu_keyboard,
)
from bot.states import ADDING_SPARK_DESCRIPTION, ADDING_SPARK_TITLE, SECTION
from bot.storage import find_item, make_id, storage
from bot.utils import clamp_page, ensure_access, remember_current_chat

_safe_edit_message: Callable[..., Awaitable[None]] | None = None


SPARK_ACTIVE = "active"
SPARK_DONE = "done"


def configure_spark_handlers(*, safe_edit_message: Callable[..., Awaitable[None]]) -> None:
    global _safe_edit_message
    _safe_edit_message = safe_edit_message


def _ensure_spark_structure(data: dict) -> dict:
    spark = data.setdefault("spark", {})
    if not isinstance(spark, dict):
        spark = {SPARK_ACTIVE: [], SPARK_DONE: []}
        data["spark"] = spark
    for bucket in (SPARK_ACTIVE, SPARK_DONE):
        value = spark.get(bucket)
        if not isinstance(value, list):
            spark[bucket] = []
    return spark


def _bucket_items(data: dict, bucket: str) -> list[dict]:
    spark = _ensure_spark_structure(data)
    return spark[SPARK_DONE if bucket == SPARK_DONE else SPARK_ACTIVE]


def _normalize_bucket(bucket: str) -> str:
    return SPARK_DONE if bucket == SPARK_DONE else SPARK_ACTIVE


def _spark_item_text(item: dict, bucket: str) -> str:
    lines = [
        f"🔥 {item.get('title', 'Без названия')}",
        f"Статус: {'Завершённые' if bucket == SPARK_DONE else 'Активные'}",
    ]
    description = item.get("description") or ""
    if description:
        lines.append(f"Описание: {description}")
    return "\n".join(lines)


async def show_spark_menu(update: Update) -> int:
    if _safe_edit_message is None:
        raise RuntimeError("Spark handlers are not configured")

    query = update.callback_query
    await _safe_edit_message(query, "🔥 Искра\n\nВыберите действие:", reply_markup=spark_menu_keyboard())
    return SECTION


async def show_spark_list(update: Update, bucket: str, page: int) -> int:
    if _safe_edit_message is None:
        raise RuntimeError("Spark handlers are not configured")

    query = update.callback_query
    bucket = _normalize_bucket(bucket)
    data = storage.load()
    items = _bucket_items(data, bucket)

    if not items:
        await _safe_edit_message(
            query,
            f"{spark_bucket_title(bucket)}\n\nПока пусто.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("➕ Добавить активность", callback_data="spark:add")],
                    [InlineKeyboardButton("⬅️ Назад", callback_data="spark:menu")],
                    [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
                ]
            ),
        )
        return SECTION

    current_page = clamp_page(page, len(items))
    await _safe_edit_message(
        query,
        f"{spark_bucket_title(bucket)}\n\nНажми на активность, чтобы открыть карточку.",
        reply_markup=spark_list_keyboard(items, bucket, current_page),
    )
    return SECTION


async def show_spark_item(update: Update, bucket: str, item_id: str, page: int) -> int:
    if _safe_edit_message is None:
        raise RuntimeError("Spark handlers are not configured")

    query = update.callback_query
    bucket = _normalize_bucket(bucket)
    data = storage.load()
    item = find_item(_bucket_items(data, bucket), item_id)
    if not item:
        await _safe_edit_message(
            query,
            "Активность не найдена.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К списку", callback_data=f"spark:list:{bucket}:{page}")]]),
        )
        return SECTION

    await _safe_edit_message(query, _spark_item_text(item, bucket), reply_markup=spark_item_keyboard(bucket, item_id, page))
    return SECTION


async def add_spark_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Название активности не должно быть пустым. Попробуй ещё раз:")
        return ADDING_SPARK_TITLE

    context.user_data["spark_title"] = title
    await update.message.reply_text("Теперь отправь описание. Если не нужно, напиши -")
    return ADDING_SPARK_DESCRIPTION


async def add_spark_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    description = (update.message.text or "").strip()
    if description == "-":
        description = ""

    item = {
        "id": make_id(),
        "title": context.user_data.get("spark_title", "Без названия"),
        "description": description,
    }

    data = storage.load()
    spark = _ensure_spark_structure(data)
    spark[SPARK_ACTIVE].append(item)
    storage.save(data)

    context.user_data.pop("spark_title", None)
    context.user_data["active_section"] = "spark"
    await update.message.reply_text(
        f"Активность сохранена:\n\n{_spark_item_text(item, SPARK_ACTIVE)}",
        reply_markup=spark_item_keyboard(SPARK_ACTIVE, item["id"], 0),
    )
    return SECTION


async def spark_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if _safe_edit_message is None:
        raise RuntimeError("Spark handlers are not configured")

    if not await ensure_access(update):
        return ConversationHandler.END
    await remember_current_chat(update)

    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")
    if parts[0] != "spark":
        return SECTION

    action = parts[1] if len(parts) > 1 else ""

    if action == "menu":
        return await show_spark_menu(update)

    if action == "add":
        await _safe_edit_message(query, "Отправь название активности:")
        return ADDING_SPARK_TITLE

    if action == "list" and len(parts) == 4:
        bucket = _normalize_bucket(parts[2])
        return await show_spark_list(update, bucket, int(parts[3]))

    if action == "view" and len(parts) == 5:
        bucket = _normalize_bucket(parts[2])
        return await show_spark_item(update, bucket, parts[3], int(parts[4]))

    if action == "toggle" and len(parts) == 5:
        bucket = _normalize_bucket(parts[2])
        item_id = parts[3]
        page = int(parts[4])

        data = storage.load()
        source_bucket = _bucket_items(data, bucket)
        item = find_item(source_bucket, item_id)
        if not item:
            await _safe_edit_message(
                query,
                "Не удалось обновить статус: активность не найдена.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К списку", callback_data=f"spark:list:{bucket}:{page}")]]),
            )
            return SECTION

        source_bucket[:] = [entry for entry in source_bucket if entry.get("id") != item_id]
        target_bucket = SPARK_DONE if bucket == SPARK_ACTIVE else SPARK_ACTIVE
        _bucket_items(data, target_bucket).append(item)
        storage.save(data)

        return await show_spark_item(update, target_bucket, item_id, 0)

    if action == "delete_confirm" and len(parts) == 5:
        bucket = _normalize_bucket(parts[2])
        item_id = parts[3]
        page = int(parts[4])

        data = storage.load()
        item = find_item(_bucket_items(data, bucket), item_id)
        if not item:
            await _safe_edit_message(query, "Не удалось найти активность для удаления.", reply_markup=spark_menu_keyboard())
            return SECTION

        await _safe_edit_message(
            query,
            f"{_spark_item_text(item, bucket)}\n\nТочно удалить?",
            reply_markup=spark_delete_confirm_keyboard(bucket, item_id, page),
        )
        return SECTION

    if action == "delete" and len(parts) == 5:
        bucket = _normalize_bucket(parts[2])
        item_id = parts[3]
        requested_page = int(parts[4])

        data = storage.load()
        items = _bucket_items(data, bucket)
        item = find_item(items, item_id)
        if not item:
            await _safe_edit_message(query, "Не удалось удалить: активность не найдена.", reply_markup=spark_menu_keyboard())
            return SECTION

        items[:] = [entry for entry in items if entry.get("id") != item_id]
        storage.save(data)

        if not items:
            await _safe_edit_message(
                query,
                f"{spark_bucket_title(bucket)}\n\nАктивность удалена. Список пуст.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("➕ Добавить активность", callback_data="spark:add")],
                        [InlineKeyboardButton("⬅️ Назад", callback_data="spark:menu")],
                        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
                    ]
                ),
            )
            return SECTION

        page = clamp_page(requested_page, len(items))
        await _safe_edit_message(
            query,
            f"{spark_bucket_title(bucket)}\n\nАктивность удалена.",
            reply_markup=spark_list_keyboard(items, bucket, page),
        )
        return SECTION

    return SECTION
