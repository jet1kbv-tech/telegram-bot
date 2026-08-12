"""Pure, reusable presentation helpers for event attachments."""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

SEMANTIC_LABELS = {
    "voucher": "🏨 Ваучер / проживание", "accommodation": "🏨 Ваучер / проживание",
    "insurance": "🛡 Страховка", "reservation": "📅 Бронь", "other": "📄 Документ",
}
TRANSPORT = {
    "train": ("🚆", "Билет на поезд"), "plane": ("✈️", "Билет на самолёт"),
    "bus": ("🚌", "Билет на автобус"), "other": ("🎟", "Билет"),
}
PERSON_LABELS = {"vova": "Вова", "sasha": "Саша", "both": "Вова и Саша"}
MONTHS = ("", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")


def transport_icon_label(transport_type: str | None) -> tuple[str, str]:
    return TRANSPORT.get(transport_type or "other", TRANSPORT["other"])


def semantic_label(item: dict[str, Any]) -> str:
    if item.get("semantic_type") == "transport_ticket":
        icon, label = transport_icon_label(item.get("transport_type"))
        return f"{icon} {label}"
    return SEMANTIC_LABELS.get(item.get("semantic_type"), SEMANTIC_LABELS["other"])


def attachment_list_title(item: dict[str, Any]) -> str:
    if item.get("semantic_type") != "transport_ticket":
        return semantic_label(item)
    icon, fallback = transport_icon_label(item.get("transport_type"))
    origin, destination = item.get("origin"), item.get("destination")
    title = f"{icon} {origin} → {destination}" if origin and destination else (
        f"{icon} Билет · {destination}" if destination else f"{icon} {fallback}")
    if origin and destination and item.get("date"):
        parsed = date.fromisoformat(item["date"])
        title += f" · {parsed:%d.%m}"
    return title


def attachment_list_titles(items: list[dict[str, Any]]) -> list[str]:
    """Number only colliding labels, in the stable attachment order."""
    base = [attachment_list_title(item) for item in items]
    counts = Counter(base)
    seen: Counter[str] = Counter()
    result = []
    for title in base:
        seen[title] += 1
        result.append(f"{title} · #{seen[title]}" if counts[title] > 1 else title)
    return result


def attachment_detail_text(item: dict[str, Any]) -> str:
    lines = [semantic_label(item)]
    if item.get("semantic_type") == "transport_ticket":
        origin, destination = item.get("origin"), item.get("destination")
        if origin and destination: lines += ["", f"{origin} → {destination}"]
        elif origin: lines += ["", f"Откуда: {origin}"]
        elif destination: lines += ["", f"Куда: {destination}"]
        if item.get("date"):
            parsed = date.fromisoformat(item["date"])
            lines.append(f"{parsed.day} {MONTHS[parsed.month]} {parsed.year}")
        if item.get("departure_time"): lines.append(f"Отправление: {item['departure_time']}")
        if item.get("person"): lines.append(f"Для: {PERSON_LABELS.get(item['person'], item['person'])}")
    return "\n".join(lines)
