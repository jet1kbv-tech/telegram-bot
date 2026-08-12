from typing import Any

from bot.config import PAGE_SIZE, SECTION_CONFIG
from bot.handlers.afisha import build_afisha_item_text
from bot.utils import item_status_label, owner_label

FILM_REACTION_LABELS = {
    "like": "❤️ Понравилось",
    "neutral": "😐 Нормально",
    "dislike": "👎 Не понравилось",
}


def build_item_text(section: str, item: dict[str, Any]) -> str:
    if section == "films":
        media_type = str(item.get("media_type") or "")
        icon = "📺" if media_type == "tv" else "🎬"
        type_label = {"movie": "Фильм", "tv": "Сериал"}.get(media_type, "")
        lines = [f"{icon} {item['title']}"]
        localized_title = str(item.get("localized_title") or "")
        if localized_title and localized_title.casefold() != str(item["title"]).casefold():
            lines.append(localized_title)
        original_title = str(item.get("original_title") or "")
        shown_titles = {str(item["title"]).casefold(), localized_title.casefold()}
        if original_title and original_title.casefold() not in shown_titles:
            lines.append(original_title)
        facts = [type_label] if type_label else []
        if item.get("year"):
            facts.append(str(item["year"]))
        if facts:
            lines.extend(["", " · ".join(facts)])
        genres = [str(genre) for genre in item.get("genres", []) if genre]
        if genres:
            lines.append(" · ".join(genres))
        if item.get("external_rating") is not None:
            lines.append(f"⭐ {item['external_rating']:g}")
        if item.get("description"):
            lines.extend(["", str(item["description"])])
        lines.extend([
            "",
            f"Статус: {item_status_label(section, item['status'])}",
            f"Добавил: {item.get('added_by', 'unknown')}",
        ])
        if item.get("comment"):
            lines.append(f"Комментарий: {item['comment']}")
        if item.get("status") == "watched":
            reactions = item.get("reactions") if isinstance(item.get("reactions"), dict) else {}
            lines.extend([
                "",
                "Оценки:",
                f"Вова: {FILM_REACTION_LABELS.get(reactions.get('vova'), '—')}",
                f"Саша: {FILM_REACTION_LABELS.get(reactions.get('sasha'), '—')}",
            ])
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
