from typing import Awaitable, Callable

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.states import MENU, SECTION
from bot.utils import ensure_access, get_user_name, remember_current_chat

_main_menu_keyboard: Callable[[], InlineKeyboardMarkup] | None = None
_safe_edit_message: Callable[..., Awaitable[None]] | None = None


def configure_common_handlers(
    *,
    main_menu_keyboard: Callable[[], InlineKeyboardMarkup],
    safe_edit_message: Callable[..., Awaitable[None]],
) -> None:
    global _main_menu_keyboard, _safe_edit_message
    _main_menu_keyboard = main_menu_keyboard
    _safe_edit_message = safe_edit_message


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    username = user.username if user and user.username else "нет username"
    text = f"id: {user.id}\nusername: {username}" if user else "Пользователь не найден"
    await update.message.reply_text(text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    if _main_menu_keyboard is None or _safe_edit_message is None:
        raise RuntimeError("Common handlers are not configured.")

    await remember_current_chat(update)
    context.user_data.clear()
    name = get_user_name(update)
    text = f"Привет, {name}! Это ваш бот для общих списков.\n\nЧто хочешь открыть?"
    if update.message:
        await update.message.reply_text(text, reply_markup=_main_menu_keyboard())
    elif update.callback_query:
        await _safe_edit_message(update.callback_query, text, reply_markup=_main_menu_keyboard())
    return MENU


async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    await remember_current_chat(update)
    query = update.callback_query
    await query.answer()
    return await start(update, context)


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return SECTION


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END

    if _main_menu_keyboard is None:
        raise RuntimeError("Common handlers are not configured.")

    await remember_current_chat(update)
    context.user_data.clear()
    await update.message.reply_text("Окей, возвращаемся в главное меню.", reply_markup=_main_menu_keyboard())
    return MENU
