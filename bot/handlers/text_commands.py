from __future__ import annotations

from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, filters

CallbackRouter = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[int]]

_COMMAND_TO_CALLBACK: dict[str, str] = {
    "меню": "menu:main",
    "чем": "activity:menu",
    "фильм": "menu|films",
    "досуг": "menu|leisure",
    "москва": "places:moscow",
    "города": "places:cities:0",
}

_menu_router: CallbackRouter | None = None
_section_router: CallbackRouter | None = None
_places_callback_router: CallbackRouter | None = None


class _SyntheticCallbackQuery:
    def __init__(self, update: Update, data: str) -> None:
        self._update = update
        self.data = data

    async def answer(self) -> None:
        return None

    async def edit_message_text(self, text: str, reply_markup=None) -> None:
        message = self._update.effective_message
        if message is not None:
            await message.reply_text(text, reply_markup=reply_markup)


class _SyntheticUpdate:
    def __init__(self, update: Update, callback_query: _SyntheticCallbackQuery) -> None:
        self._update = update
        self.callback_query = callback_query

    def __getattr__(self, item):
        return getattr(self._update, item)


def configure_text_commands(*, menu_router: CallbackRouter, section_router: CallbackRouter, places_callback_router: CallbackRouter) -> None:
    global _menu_router, _section_router, _places_callback_router
    _menu_router = menu_router
    _section_router = section_router
    _places_callback_router = places_callback_router


def quick_text_command_filter() -> filters.BaseFilter:
    pattern = r"(?i)^\s*(меню|чем|фильм|досуг|москва|города)\s*$"
    return filters.Regex(pattern)


async def quick_text_command_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if _menu_router is None or _section_router is None or _places_callback_router is None:
        raise RuntimeError("Text command handlers are not configured.")

    text = ((update.effective_message.text if update.effective_message else "") or "").strip().casefold()
    callback_data = _COMMAND_TO_CALLBACK.get(text)
    if callback_data is None:
        return ConversationHandler.END

    context.user_data.clear()

    if callback_data.startswith("places:"):
        target_router = _places_callback_router
    elif callback_data.startswith("menu|"):
        target_router = _menu_router
    else:
        target_router = _section_router

    synthetic_query = _SyntheticCallbackQuery(update, callback_data)
    synthetic_update = _SyntheticUpdate(update, synthetic_query)
    return await target_router(synthetic_update, context)
