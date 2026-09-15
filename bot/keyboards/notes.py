from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils import paginate_items


def notes_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Новая заметка", callback_data="notes:add")],
        [InlineKeyboardButton("📋 Мои заметки", callback_data="notes:list:0")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="more:menu")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])


def note_preview(text: str, limit: int = 42) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[:limit - 3].rstrip() + "..."


def notes_list_keyboard(notes: list[dict[str, str]], page: int) -> InlineKeyboardMarkup:
    shown, page, total_pages = paginate_items(notes, page)
    rows = [[InlineKeyboardButton(note_preview(note["text"]),
             callback_data=f"notes:view:{note['id']}:{page}")] for note in shown]
    pagination = []
    if page > 0:
        pagination.append(InlineKeyboardButton("⬅️", callback_data=f"notes:list:{page - 1}"))
    if total_pages > 1:
        pagination.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="notes:noop"))
    if page < total_pages - 1:
        pagination.append(InlineKeyboardButton("➡️", callback_data=f"notes:list:{page + 1}"))
    if pagination:
        rows.append(pagination)
    rows.extend([
        [InlineKeyboardButton("➕ Новая заметка", callback_data="notes:add")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="notes:menu")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])
    return InlineKeyboardMarkup(rows)


def note_card_keyboard(note_id: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Изменить", callback_data=f"notes:edit:{note_id}:{page}")],
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"notes:delete_confirm:{note_id}:{page}")],
        [InlineKeyboardButton("⬅️ К заметкам", callback_data=f"notes:list:{page}")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])


def note_delete_keyboard(note_id: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Да, удалить", callback_data=f"notes:delete:{note_id}:{page}")],
        [InlineKeyboardButton("↩️ Отмена", callback_data=f"notes:view:{note_id}:{page}")],
    ])
