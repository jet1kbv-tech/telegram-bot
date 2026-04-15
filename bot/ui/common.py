from typing import Any

from bot.config import PAGE_SIZE, SECTION_CONFIG
from bot.handlers.afisha import build_afisha_item_text
from bot.storage import format_average_rating
from bot.utils import item_status_label, owner_label


def build_item_text(section: str, item: dict[str, Any]) -> str:
    if section == "films":
        lines = [
            f"🎬 {item['title']}",
            f"Статус: {item_status_label(section, item['status'])}",
            f"Добавил: {item.get('added_by', 'unknown')}",
        ]
        if item.get("status") == "watched":
            if item.get("sasha_rating") is not None:
                lines.append(f"Оценка Саши: {item['sasha_rating']}/10")
            if item.get("vova_rating") is not None:
                lines.append(f"Оценка Вовы: {item['vova_rating']}/10")
            average = format_average_rating(item)
            if average is not None:
                lines.append(f"Средний рейтинг: {average}/10")
            elif item.get("legacy_rating") is not None:
                lines.append(f"Рейтинг: {item['legacy_rating']}/10")
        if item.get("comment"):
            lines.append(f"Комментарий: {item['comment']}")
        return "\n".join(lines)

    if section == "wishlist":
        lines = [
            f"🎁 {item['title']}",
            f"Чей вишлист: {owner_label(item.get('owner', 'unknown'))}",
            f"Статус: {item_status_label(section, item['status'])}",
        ]
        if item.get("reserved_by"):
            lines.append(f"Кто отметил подарок: {item['reserved_by']}")
        if item.get("link"):
            lines.append(f"Ссылка: {item['link']}")
        if item.get("comment"):
            lines.append(f"Комментарий: {item['comment']}")
        return "\n".join(lines)

    if section == "leisure":
        lines = [
            f"✨ {item['title']}",
            f"Статус: {item_status_label(section, item['status'])}",
        ]
        if item.get("comment"):
            lines.append(f"Комментарий: {item['comment']}")
        return "\n".join(lines)

    if section == "afisha":
        return build_afisha_item_text(item)

    if section == "backlog":
        lines = [
            f"🧩 {item['title']}",
            f"Статус: {item_status_label(section, item['status'])}",
        ]
        if item.get("description"):
            lines.append(f"Описание: {item['description']}")
        return "\n".join(lines)

    return "Элемент"


def build_list_text(
    section: str,
    items: list[dict[str, Any]],
    page: int,
    total_pages: int,
    owner: str | None = None,
    status_filter: str | None = None,
) -> str:
    title = SECTION_CONFIG[section]["title"]
    if section == "wishlist" and owner:
        title = f"🎁 Вишлист · {owner_label(owner)}"
    elif section == "films" and status_filter:
        title = f"🎬 Фильмы · {item_status_label(section, status_filter)}"
    elif section == "backlog" and status_filter:
        title = f"🧩 Бэклог · {item_status_label(section, status_filter)}"

    total_items = len(items)
    if total_items == 0:
        return f"{title}\n\n{SECTION_CONFIG[section]['empty_text']}"

    start_num = page * PAGE_SIZE + 1
    end_num = min(total_items, start_num + PAGE_SIZE - 1)
    return (
        f"{title}\n\n"
        f"Элементы {start_num}–{end_num} из {total_items}.\n"
        f"Нажми на пункт, чтобы открыть карточку, сменить статус или удалить его."
    )
