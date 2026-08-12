from pathlib import Path
from typing import Any
import math
import os


def _positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    value = default if raw is None else float(raw)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value

DATA_FILE = Path("data.json")
PAGE_SIZE = 10
NOTIFY_LOOKAHEAD_MIN = 23 * 60
NOTIFY_LOOKAHEAD_MAX = 25 * 60
NOTIFICATION_CHECK_INTERVAL = 60 * 60
BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"
AI_INTENT_TIMEOUT_SECONDS = float(os.getenv("AI_INTENT_TIMEOUT_SECONDS", "10"))
AI_ATTACHMENT_TIMEOUT_SECONDS = _positive_float_env("AI_ATTACHMENT_TIMEOUT_SECONDS", 25)
AI_ATTACHMENT_MAX_BYTES = _positive_int_env("AI_ATTACHMENT_MAX_BYTES", 8 * 1024 * 1024)
AI_PROPOSAL_TTL_SECONDS = int(os.getenv("AI_PROPOSAL_TTL_SECONDS", "900"))
AI_MAX_CLARIFICATIONS = int(os.getenv("AI_MAX_CLARIFICATIONS", "3"))
AFISHA_MORNING_START_HOUR = 7
AFISHA_MORNING_END_HOUR = 12

# Заполни usernames без @
ALLOWED_USERS = {
    "wp_bvv": {"name": "Вова", "wishlist_owner": "vova", "gender": "male"},
    "privetnormalno": {"name": "Саша", "wishlist_owner": "sasha", "gender": "female"},
}

KNOWN_WISHLIST_OWNERS = {"vova", "sasha", "unknown"}

FILM_STATUSES = ["want", "watched"]
WISHLIST_STATUSES = ["active", "gifted"]
LEISURE_STATUSES = ["want", "done"]
AFISHA_STATUSES = ["active", "done"]
BACKLOG_STATUSES = ["todo", "done"]

SECTION_CONFIG: dict[str, dict[str, Any]] = {
    "films": {
        "title": "🎬 Фильмы",
        "empty_text": "Пока пусто. Добавьте первый фильм.",
        "statuses": FILM_STATUSES,
        "status_labels": {
            "want": "Непросмотренные",
            "watched": "Просмотренные",
        },
    },
    "wishlist": {
        "title": "🎁 Вишлист",
        "empty_text": "Пока пусто.",
        "statuses": WISHLIST_STATUSES,
        "status_labels": {
            "active": "Актуально",
            "gifted": "Подарено",
        },
    },
    "leisure": {
        "title": "✨ Досуг",
        "empty_text": "Пока пусто. Добавьте первую идею.",
        "statuses": LEISURE_STATUSES,
        "status_labels": {
            "want": "Хотим сделать",
            "done": "Сделано",
        },
    },
    "afisha": {
        "title": "🗓 Афиша",
        "empty_text": "Пока нет актуальных событий.",
        "statuses": AFISHA_STATUSES,
        "status_labels": {
            "active": "Не выполнено",
            "done": "Выполнено",
        },
    },
    "backlog": {
        "title": "🧩 Бэклог",
        "empty_text": "Пока фич нет.",
        "statuses": BACKLOG_STATUSES,
        "status_labels": {
            "todo": "К реализации",
            "done": "Реализовано",
        },
    },
}

WISHLIST_OWNER_LABELS = {
    "vova": "Вова",
    "sasha": "Саша",
    "unknown": "Без владельца",
}
