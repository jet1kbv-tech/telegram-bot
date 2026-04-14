from typing import Any

from telegram import Update

from bot.config import ALLOWED_USERS, PAGE_SIZE, SECTION_CONFIG, WISHLIST_OWNER_LABELS
from bot.storage import storage


def get_username(update: Update) -> str:
    user = update.effective_user
    if not user or not user.username:
        return ""
    return user.username


def get_allowed_profile(update: Update) -> dict[str, str] | None:
    return ALLOWED_USERS.get(get_username(update))


async def ensure_access(update: Update) -> bool:
    profile = get_allowed_profile(update)
    if profile:
        return True
    text = (
        "У этого бота закрытый доступ.\n\n"
        "Попроси владельца добавить твой Telegram username в ALLOWED_USERS."
    )
    if update.message:
        await update.message.reply_text(text)
    elif update.callback_query:
        await update.callback_query.answer("Нет доступа", show_alert=True)
    return False


def get_user_name(update: Update) -> str:
    profile = get_allowed_profile(update)
    if profile:
        return profile["name"]
    user = update.effective_user
    if not user:
        return "unknown"
    return user.first_name or user.username or str(user.id)


def get_gender_by_username(username: str) -> str:
    profile = ALLOWED_USERS.get(username) or {}
    return str(profile.get("gender") or "unknown")


def reminder_forget_word(username: str) -> str:
    gender = get_gender_by_username(username)
    if gender == "female":
        return "забыла"
    return "забыл"


def get_wishlist_owner_by_user(update: Update) -> str:
    profile = get_allowed_profile(update)
    return profile["wishlist_owner"] if profile else "unknown"


def get_other_wishlist_owner(update: Update) -> str:
    current_owner = get_wishlist_owner_by_user(update)
    if current_owner == "vova":
        return "sasha"
    if current_owner == "sasha":
        return "vova"
    return "unknown"


def owner_label(owner: str) -> str:
    return WISHLIST_OWNER_LABELS.get(owner, owner)


def item_status_label(section: str, status: str) -> str:
    return SECTION_CONFIG[section]["status_labels"].get(status, status)


def upsert_user_chat_id(data: dict[str, Any], username: str, chat_id: int) -> None:
    if not username or not isinstance(chat_id, int):
        return
    data.setdefault("meta", {}).setdefault("user_chats", {})[username] = chat_id


async def remember_current_chat(update: Update) -> None:
    username = get_username(update)
    chat = update.effective_chat
    if not username or not chat:
        return

    def mutator(data: dict[str, Any]):
        upsert_user_chat_id(data, username, chat.id)
        return None

    storage.update(mutator)


def clamp_page(page: int, total_items: int) -> int:
    if total_items <= 0:
        return 0
    last_page = (total_items - 1) // PAGE_SIZE
    return max(0, min(page, last_page))


def paginate_items(items: list[dict[str, Any]], page: int) -> tuple[list[dict[str, Any]], int, int]:
    page = clamp_page(page, len(items))
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    return items[start:end], page, total_pages
