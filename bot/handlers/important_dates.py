from __future__ import annotations

from datetime import date
from typing import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import BOT_TIMEZONE
from bot.services.ics_birthdays import MAX_ICS_BYTES, ImportCandidate, parse_ics_birthdays, split_duplicates
from bot.services.important_dates import (age_on_next_occurrence, days_until, format_date, is_visible,
    next_occurrence, parse_birthday_date, sort_birthdays)
from bot.services.nl_dates import zoned_now
from bot.states import (BIRTHDAY_DATE, BIRTHDAY_IMPORT_FILE, BIRTHDAY_TITLE, BIRTHDAY_YEAR)
from bot.states import SECTION
from bot.storage import make_id, normalize_important_date, storage
from bot.utils import ensure_access, get_wishlist_owner_by_user, normalize_entity_title

_safe_edit: Callable[..., Awaitable[None]] | None = None
SESSION = "birthday_session"
IMPORT = "birthday_import"


def configure_important_dates_handlers(*, safe_edit_message):
    global _safe_edit
    _safe_edit = safe_edit_message


def _today() -> date:
    return zoned_now(BOT_TIMEZONE).date()


def birthdays_keyboard(items=()):
    rows = [[InlineKeyboardButton(f"🎂 {item['title']}", callback_data=f"birthday:view:{item['id']}")]
            for item in items]
    rows += [[InlineKeyboardButton("➕ Добавить", callback_data="birthday:add")],
             [InlineKeyboardButton("📥 Импортировать", callback_data="birthday:import")],
             [InlineKeyboardButton("⬅️ К ближайшему", callback_data="upcoming:7")],
             [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]
    return InlineKeyboardMarkup(rows)


def render_birthday_list(items, today):
    if not items:
        return "🎂 Дни рождения\n\nПока ни одного дня рождения не добавлено."
    lines = ["🎂 Дни рождения", "", "Ближайшие:"]
    for item in sort_birthdays(items, today):
        days, age = days_until(item, today), age_on_next_occurrence(item, today)
        if days == 0:
            line = f"Сегодня — {item['title']} 🎉"
        else:
            line = f"{format_date(item)} — {item['title']} · через {days} дней"
        if age is not None:
            line += f" · исполнится {age}"
        lines.append(line)
    return "\n".join(lines)


async def show_birthdays(update, context):
    actor = get_wishlist_owner_by_user(update)
    items = [item for item in storage.load().get("important_dates", []) if is_visible(item, actor)]
    await _safe_edit(update.callback_query, render_birthday_list(items, _today()),
                     reply_markup=birthdays_keyboard(sort_birthdays(items, _today())))
    return SECTION


def _visible(item_id, actor):
    return next((item for item in storage.load().get("important_dates", [])
                 if item.get("id") == item_id and is_visible(item, actor)), None)


def detail_text(item, today):
    occurrence, age = next_occurrence(item, today), age_on_next_occurrence(item, today)
    lines = [f"🎂 {item['title']}", "", format_date(item, True),
             f"Следующий день рождения: {format_date({**item, 'day': occurrence.day, 'month': occurrence.month, 'year': occurrence.year}, True)}"]
    if age is not None:
        lines.append(f"Исполнится: {age}")
    return "\n".join(lines)


def detail_keyboard(item_id):
    return InlineKeyboardMarkup([[InlineKeyboardButton("✏️ Изменить", callback_data=f"birthday:edit:{item_id}")],
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"birthday:delete_confirm:{item_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="birthday:list")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]])


async def birthday_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_access(update): return ConversationHandler.END
    query = update.callback_query; await query.answer()
    parts = query.data.split(":"); action = parts[1]; actor = get_wishlist_owner_by_user(update)
    if action == "list": return await show_birthdays(update, context)
    if action == "add":
        context.user_data[SESSION] = {"mode": "add"}
        await _safe_edit(query, "Как зовут человека?", reply_markup=_cancel()); return BIRTHDAY_TITLE
    if action == "import":
        context.user_data.pop(IMPORT, None)
        await _safe_edit(query, "📥 Импорт дней рождения\n\nОтправь файл календаря в формате .ics.\n\nЯ найду в нём дни рождения, покажу предварительный результат и ничего не сохраню без подтверждения.", reply_markup=_cancel())
        return BIRTHDAY_IMPORT_FILE
    if action == "cancel":
        context.user_data.pop(SESSION, None); context.user_data.pop(IMPORT, None)
        return await show_birthdays(update, context)
    if action == "confirm":
        draft = context.user_data.pop(SESSION, None)
        if not draft: return await show_birthdays(update, context)
        record = normalize_important_date({**draft, "id": make_id(), "type": "birthday", "visibility": "shared", "note": "", "created_by": actor})
        if record: storage.update(lambda data: data.setdefault("important_dates", []).append(record))
        return await show_birthdays(update, context)
    if action == "import_confirm":
        pending = context.user_data.pop(IMPORT, [])
        def mutate(data):
            visible = [x for x in data.get("important_dates", []) if is_visible(x, actor)]
            fresh, duplicates = split_duplicates([ImportCandidate(**x) for x in pending], visible)
            for x in fresh: data.setdefault("important_dates", []).append(normalize_important_date({"id": make_id(), "type": "birthday", "title": x.title, "month": x.month, "day": x.day, "year": x.year, "visibility": "shared", "note": "", "created_by": actor}))
            return len(fresh), len(duplicates)
        (added, duplicate), _ = storage.update(mutate)
        await _safe_edit(query, f"✅ Импортировано дней рождения: {added}\n⚠️ Пропущено как уже существующие: {duplicate}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎂 К дням рождения", callback_data="birthday:list")]])); return SECTION
    if action == "import_view":
        pending = context.user_data.get(IMPORT, [])
        lines = ["👀 Новые дни рождения", ""]
        lines.extend(f"• {item['title']} — {item['day']}.{item['month']:02d}" for item in pending[:50])
        if len(pending) > 50:
            lines.append(f"\nИ ещё {len(pending) - 50}…")
        await _safe_edit(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Импортировать {len(pending)}", callback_data="birthday:import_confirm")],
            *_cancel().inline_keyboard,
        ]))
        return SECTION
    item_id = parts[2] if len(parts) > 2 else ""; item = _visible(item_id, actor)
    if not item:
        await _safe_edit(query, "День рождения не найден или недоступен.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="birthday:list")]])); return SECTION
    if action == "view":
        await _safe_edit(query, detail_text(item, _today()), reply_markup=detail_keyboard(item_id)); return SECTION
    if action == "delete_confirm":
        await _safe_edit(query, f"Удалить день рождения «{item['title']}»?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Да, удалить", callback_data=f"birthday:delete:{item_id}")], [InlineKeyboardButton("❌ Отмена", callback_data=f"birthday:view:{item_id}")]])); return SECTION
    if action == "delete":
        def remove_visible(data):
            current = next((x for x in data["important_dates"]
                            if x["id"] == item_id and is_visible(x, actor)), None)
            if current:
                data["important_dates"].remove(current)
            return current is not None
        storage.update(remove_visible)
        return await show_birthdays(update, context)
    if action == "edit":
        await _safe_edit(query, f"✏️ {item['title']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👤 Имя", callback_data=f"birthday:field_title:{item_id}")], [InlineKeyboardButton("📅 Дату", callback_data=f"birthday:field_date:{item_id}")], [InlineKeyboardButton("🎂 Год рождения", callback_data=f"birthday:field_year:{item_id}")], [InlineKeyboardButton("⬅️ Назад", callback_data=f"birthday:view:{item_id}")]])); return SECTION
    if action.startswith("field_"):
        field = action[6:]; context.user_data[SESSION] = {"mode": "edit", "id": item_id, "field": field}
        prompts = {"title": "Введи новое имя.", "date": "Введи новую дату рождения.", "year": "Введи год рождения или «Удалить»."}
        await _safe_edit(query, prompts[field], reply_markup=_cancel()); return {"title": BIRTHDAY_TITLE, "date": BIRTHDAY_DATE, "year": BIRTHDAY_YEAR}[field]
    return SECTION


def _cancel():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="birthday:cancel")]])


async def birthday_title_input(update, context):
    title = normalize_entity_title(" ".join(update.message.text.strip().split()))
    if not title: await update.message.reply_text("Имя не может быть пустым."); return BIRTHDAY_TITLE
    draft = context.user_data.get(SESSION, {})
    if draft.get("mode") == "edit": return await _edit_value(update, context, title)
    draft["title"] = title
    await update.message.reply_text("Когда день рождения?", reply_markup=_cancel()); return BIRTHDAY_DATE


async def birthday_date_input(update, context):
    try: month, day, year = parse_birthday_date(update.message.text)
    except ValueError:
        await update.message.reply_text("Не получилось распознать дату. Например: 3 мая, 03.05 или 03.05.1994."); return BIRTHDAY_DATE
    draft = context.user_data.get(SESSION, {})
    if draft.get("mode") == "edit": return await _edit_value(update, context, (month, day, year))
    draft.update(month=month, day=day, year=year)
    if year is not None: return await _confirmation(update, draft)
    await update.message.reply_text("Знаешь год рождения? Введи YYYY или нажми «Пропустить».", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить", callback_data="birthday:skip_year")], *_cancel().inline_keyboard])); return BIRTHDAY_YEAR


async def birthday_year_input(update, context):
    text = update.message.text.strip().casefold(); draft = context.user_data.get(SESSION, {})
    if draft.get("mode") == "edit" and text in {"удалить", "убрать", "нет"}: return await _edit_value(update, context, None)
    try:
        year = int(text)
    except (ValueError, OverflowError):
        await update.message.reply_text("Введи год четырьмя цифрами."); return BIRTHDAY_YEAR
    if draft.get("mode") == "edit":
        # Month/day must come from the currently authorized stored record, not
        # from conversation data, which may be stale or user-controlled.
        actor = get_wishlist_owner_by_user(update)
        current = _visible(str(draft.get("id") or ""), actor)
        if current is None:
            context.user_data.pop(SESSION, None)
            await update.message.reply_text("День рождения не найден или недоступен.")
            return SECTION
        try:
            date(year, current["month"], current["day"])
        except ValueError:
            await update.message.reply_text(
                "Этот год не подходит для указанной даты рождения. Введи другой год."
            )
            return BIRTHDAY_YEAR
        return await _edit_value(update, context, year)
    try:
        date(year, int(draft.get("month", 1)), int(draft.get("day", 1)))
    except (ValueError, OverflowError):
        await update.message.reply_text("Этот год не подходит для указанной даты рождения.")
        return BIRTHDAY_YEAR
    draft["year"] = year; return await _confirmation(update, draft)


async def birthday_year_callback(update, context):
    await update.callback_query.answer(); draft = context.user_data.get(SESSION, {}); draft["year"] = None
    return await _confirmation(update.callback_query, draft, edit=True)


async def _confirmation(target, draft, edit=False):
    text = f"🎂 {draft['title']}\n{format_date(draft, True)}"
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Сохранить", callback_data="birthday:confirm")], *_cancel().inline_keyboard])
    if edit: await _safe_edit(target, text, reply_markup=markup)
    else: await target.message.reply_text(text, reply_markup=markup)
    return SECTION


async def _edit_value(update, context, value):
    draft = context.user_data.get(SESSION, {}); actor = get_wishlist_owner_by_user(update); field = draft["field"]
    def mutate(data):
        item = next((x for x in data["important_dates"] if x["id"] == draft["id"] and is_visible(x, actor)), None)
        if not item: return "missing"
        if field == "date":
            month, day, supplied_year = value
            candidate_year = supplied_year if supplied_year is not None else item.get("year")
            if candidate_year is not None:
                try: date(candidate_year, month, day)
                except ValueError: return "invalid"
            item["month"], item["day"] = month, day
            if supplied_year is not None: item["year"] = supplied_year
        elif field == "year":
            if value is not None:
                try: date(value, item["month"], item["day"])
                except ValueError: return "invalid"
            item[field] = value
        else: item[field] = value
        return "ok"
    result, data = storage.update(mutate)
    if result == "invalid":
        await update.message.reply_text("Значение не подходит для этой даты рождения. Попробуй ещё раз.")
        return {"date": BIRTHDAY_DATE, "year": BIRTHDAY_YEAR}.get(field, BIRTHDAY_TITLE)
    context.user_data.pop(SESSION, None)
    updated = next((item for item in data.get("important_dates", [])
                    if item.get("id") == draft.get("id") and is_visible(item, actor)), None)
    if result != "ok" or updated is None:
        await update.message.reply_text("День рождения не найден или недоступен.")
        return SECTION
    await update.message.reply_text(
        detail_text(updated, _today()),
        reply_markup=detail_keyboard(updated["id"]),
    )
    return SECTION


async def birthday_import_file(update, context):
    document = update.message.document
    if not document or not (document.file_name or "").casefold().endswith(".ics"):
        await update.message.reply_text("Нужен файл с расширением .ics."); return BIRTHDAY_IMPORT_FILE
    if document.file_size and document.file_size > MAX_ICS_BYTES:
        await update.message.reply_text("Файл слишком большой. Максимум — 3 МБ."); return BIRTHDAY_IMPORT_FILE
    try:
        raw = bytes(await (await document.get_file()).download_as_bytearray())
        result = parse_ics_birthdays(raw)
    except (ValueError, UnicodeError):
        await update.message.reply_text("Не удалось прочитать календарь. Проверь файл .ics."); return BIRTHDAY_IMPORT_FILE
    actor = get_wishlist_owner_by_user(update)
    existing = [x for x in storage.load().get("important_dates", []) if is_visible(x, actor)]
    new, duplicates = split_duplicates(result.candidates, existing)
    context.user_data[IMPORT] = [{"title": x.title, "month": x.month, "day": x.day, "year": x.year} for x in new]
    if not new:
        await update.message.reply_text(f"📥 Новых дней рождения не найдено.\n⚠️ Уже есть: {len(duplicates)}", reply_markup=_cancel()); return SECTION
    examples = "\n".join(f"• {x.title} — {x.day}.{x.month:02d}" for x in new[:3])
    text = f"📥 Найдено дней рождения: {len(result.candidates)}\n\n✅ Новых: {len(new)}\n⚠️ Уже есть: {len(duplicates)}\n❓ Пропущено: {result.skipped_count}\n\nПримеры новых:\n{examples}"
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("👀 Посмотреть новые", callback_data="birthday:import_view")],
        [InlineKeyboardButton(f"✅ Импортировать {len(new)}", callback_data="birthday:import_confirm")],
        *_cancel().inline_keyboard,
    ])); return SECTION
