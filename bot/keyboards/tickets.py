from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def tickets_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Добавить билет", callback_data="tickets:add:start")],
            [InlineKeyboardButton("📋 Активные", callback_data="tickets:list:active:0")],
            [InlineKeyboardButton("✅ Использованные", callback_data="tickets:list:used:0")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]
    )


def tickets_empty_list_keyboard(bucket: str) -> InlineKeyboardMarkup:
    title_button = "📋 Активные" if bucket == "active" else "✅ Использованные"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Добавить билет", callback_data="tickets:add:start")],
            [InlineKeyboardButton(title_button, callback_data=f"tickets:list:{bucket}:0")],
            [InlineKeyboardButton("⬅️ К билетам", callback_data="tickets:menu")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]
    )


def tickets_list_keyboard(items: list[dict], bucket: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        rows.append(
            [
                InlineKeyboardButton(
                    f"{item.get('date', '----.--.--')} {item.get('time', '--:--')} · {item.get('title', 'Без названия')}",
                    callback_data=f"tickets:view:{item.get('id', '')}:{bucket}:{page}",
                )
            ]
        )

    if total_pages > 1:
        page_row: list[InlineKeyboardButton] = []
        if page > 0:
            page_row.append(InlineKeyboardButton("⬅️", callback_data=f"tickets:list:{bucket}:{page - 1}"))
        page_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            page_row.append(InlineKeyboardButton("➡️", callback_data=f"tickets:list:{bucket}:{page + 1}"))
        rows.append(page_row)

    rows.append([InlineKeyboardButton("➕ Добавить билет", callback_data="tickets:add:start")])
    rows.append([InlineKeyboardButton("✅ Использованные", callback_data="tickets:list:used:0")])
    rows.append([InlineKeyboardButton("📋 Активные", callback_data="tickets:list:active:0")])
    rows.append([InlineKeyboardButton("⬅️ К билетам", callback_data="tickets:menu")])
    rows.append([InlineKeyboardButton("🏠 В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def ticket_card_keyboard(ticket_id: str, bucket: str, page: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("📎 Отправить вложения", callback_data=f"tickets:attachments:send:{ticket_id}:{bucket}:{page}")],
    ]
    if bucket == "active":
        rows.append([InlineKeyboardButton("✅ Отметить использованным", callback_data=f"tickets:mark_used:{ticket_id}:{page}")])
    rows.extend(
        [
            [InlineKeyboardButton("🗑️ Удалить", callback_data=f"tickets:delete_confirm:{ticket_id}:{bucket}:{page}")],
            [InlineKeyboardButton("⬅️ К списку", callback_data=f"tickets:list:{bucket}:{page}")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def ticket_delete_confirm_keyboard(ticket_id: str, bucket: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Да, удалить", callback_data=f"tickets:delete:{ticket_id}:{bucket}:{page}")],
            [InlineKeyboardButton("↩️ Нет, вернуться", callback_data=f"tickets:view:{ticket_id}:{bucket}:{page}")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]
    )


def ticket_attachments_done_keyboard(count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"✅ Готово ({count})", callback_data="tickets:add:done_attachments")],
            [InlineKeyboardButton("❌ Отмена", callback_data="tickets:menu")],
        ]
    )
