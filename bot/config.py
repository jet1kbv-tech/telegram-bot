from pathlib import Path
from typing import Any

DATA_FILE = Path("data.json")
PAGE_SIZE = 10
NOTIFY_LOOKAHEAD_MIN = 23 * 60
NOTIFY_LOOKAHEAD_MAX = 25 * 60
NOTIFICATION_CHECK_INTERVAL = 60 * 60

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
        "title": "🎁 Wishlist",
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
