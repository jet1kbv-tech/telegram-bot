"""Private, persistent reminders for the Important Dates birthday domain."""
from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from bot.services.important_dates import (age_on_next_occurrence, days_until,
                                          format_date, next_occurrence, russian_age)

logger = logging.getLogger(__name__)

REMINDER_WINDOWS = frozenset({14, 7, 0})
# Acknowledgements are useful only through the occurrence date. Keeping another
# month makes the policy operationally easy to inspect while bounding growth.
DELIVERY_RETENTION_DAYS = 32


def delivery_marker(birthday_id: str, actor: str, occurrence: date, window: int) -> str:
    """Return an opaque marker that contains no birthday title or date in logs/storage keys."""
    raw = "\x1f".join((birthday_id, actor, occurrence.isoformat(), str(window)))
    return hashlib.sha256(raw.encode()).hexdigest()


def recipient_chats(data: dict[str, Any], item: dict[str, Any],
                    actors: dict[str, dict[str, Any]]) -> tuple[tuple[str, int], ...]:
    """Resolve visibility only through the trusted configured actor/chat mapping."""
    visibility = item.get("visibility")
    chats = data.get("meta", {}).get("user_chats", {})
    recipients = []
    for username, profile in actors.items():
        actor = str(profile.get("wishlist_owner") or "")
        chat_id = chats.get(username)
        if actor in {"vova", "sasha"} and isinstance(chat_id, int) and visibility in {"shared", actor}:
            recipients.append((actor, chat_id))
    return tuple(recipients)


def render_reminder(item: dict[str, Any], window: int, today: date) -> str:
    if window == 0:
        lines = ["🎂 Сегодня день рождения!", "", f"Сегодня — {item['title']} 🎉"]
    else:
        distance = "Через неделю" if window == 7 else "Через 14 дней"
        lines = ["🎂 Скоро день рождения", "", f"{distance} — {item['title']}", format_date(item)]
    age = age_on_next_occurrence(item, today)
    if age is not None:
        lines.append(f"Исполнится {russian_age(age)}")
    return "\n".join(lines)


def _local_date(now: datetime, timezone: str) -> date:
    zone = ZoneInfo(timezone)
    localized = now.replace(tzinfo=zone) if now.tzinfo is None else now.astimezone(zone)
    return localized.date()


def _prune(deliveries: dict[str, str], today: date) -> dict[str, str]:
    cutoff = today - timedelta(days=DELIVERY_RETENTION_DAYS)
    result = {}
    for marker, raw_occurrence in deliveries.items():
        try:
            occurrence = date.fromisoformat(raw_occurrence)
        except (TypeError, ValueError):
            continue
        if cutoff <= occurrence <= today + timedelta(days=max(REMINDER_WINDOWS)):
            result[marker] = raw_occurrence
    return result


async def scan_birthday_reminders(*, storage, bot, actors: dict[str, dict[str, Any]],
                                  timezone: str, now: datetime) -> dict[str, int]:
    """Send exact-window birthday reminders and durably acknowledge each success."""
    data = storage.load()
    today = _local_date(now, timezone)
    meta = data.setdefault("meta", {})
    original = dict(meta.get("birthday_reminders", {}))
    delivered = _prune(original, today)
    counts = {"checked": 0, "due": 0, "sent": 0, "failed": 0, "duplicates_skipped": 0}

    if delivered != original:
        meta["birthday_reminders"] = delivered
        storage.save(data)

    for item in data.get("important_dates", []):
        counts["checked"] += 1
        window = days_until(item, today)
        if window not in REMINDER_WINDOWS:
            continue
        occurrence = next_occurrence(item, today)
        for actor, chat_id in recipient_chats(data, item, actors):
            counts["due"] += 1
            marker = delivery_marker(str(item["id"]), actor, occurrence, window)
            if marker in delivered:
                counts["duplicates_skipped"] += 1
                continue
            try:
                await bot.send_message(chat_id=chat_id, text=render_reminder(item, window, today))
            except Exception:
                counts["failed"] += 1
                logger.warning("birthday_reminder delivery=failed")
                continue
            delivered[marker] = occurrence.isoformat()
            meta["birthday_reminders"] = dict(sorted(delivered.items()))
            try:
                storage.save(data)
            except Exception:
                counts["failed"] += 1
                logger.error("birthday_reminder marker=failed")
                continue
            counts["sent"] += 1

    logger.info(
        "birthday_reminders checked=%s due=%s sent=%s failed=%s duplicates_skipped=%s",
        counts["checked"], counts["due"], counts["sent"], counts["failed"],
        counts["duplicates_skipped"],
    )
    return counts
