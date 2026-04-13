from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Фильмы", callback_data="menu:films")],
        [InlineKeyboardButton("🎁 Wishlist", callback_data="menu:wishlist")],
        [InlineKeyboardButton("✨ Досуг", callback_data="menu:leisure")],
        [InlineKeyboardButton("🗓 Афиша", callback_data="menu:afisha")],
        [InlineKeyboardButton("📅 Календарь", callback_data="menu:calendar")],
        [InlineKeyboardButton("📍 Места", callback_data="places:menu")],
        [InlineKeyboardButton("🧩 Бэклог", callback_data="menu:backlog")],
    ])


def places_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📍 Локации в Москве", callback_data="places:moscow_menu")],
        [InlineKeyboardButton("🌍 Города", callback_data="places:cities:0")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="main")],
    ])


def places_moscow_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📍 В планах", callback_data="places:moscow_list:planned:0")],
        [InlineKeyboardButton("✅ Посещено", callback_data="places:moscow_list:visited:0")],
        [InlineKeyboardButton("➕ Добавить", callback_data="places:moscow_add")],
        [InlineKeyboardButton("⬅️ К разделу мест", callback_data="places:menu")],
        [InlineKeyboardButton("🏠 В меню", callback_data="main")],
    ])
