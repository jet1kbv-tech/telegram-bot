from __future__ import annotations

import base64
import hashlib
import random
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import PAGE_SIZE
from bot.handlers.film_operations import delete_film, set_film_reaction, set_film_status
from bot.states import SECTION
from bot.storage import find_item, storage
from bot.utils import ensure_access, get_wishlist_owner_by_user, paginate_items

GENRELESS_KEY = "none"
GENRE_PAGE_SIZE = 10

_safe_edit_message: Callable[..., Any] | None = None
_build_item_text: Callable[[str, dict[str, Any]], str] | None = None


@dataclass(frozen=True, slots=True)
class GenreEntry:
    identity: str | None
    label: str
    count: int
    key: str


def configure_film_filter_handlers(
    *,
    safe_edit_message: Callable[..., Any],
    build_item_text: Callable[[str, dict[str, Any]], str],
) -> None:
    global _safe_edit_message, _build_item_text
    _safe_edit_message = safe_edit_message
    _build_item_text = build_item_text


def normalize_genre_display(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())


def genre_identity(value: Any) -> str:
    display = normalize_genre_display(value)
    return unicodedata.normalize("NFKC", display).casefold() if display else ""


def genre_key(identity: str) -> str:
    digest = hashlib.blake2s(identity.encode("utf-8"), digest_size=6).digest()
    # The prefix keeps real genre digests disjoint from the reserved genreless key.
    return "g" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def film_genres(film: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    raw_genres = film.get("genres")
    if not isinstance(raw_genres, list):
        return result
    for raw in raw_genres:
        display = normalize_genre_display(raw)
        identity = genre_identity(display)
        if identity and identity not in result:
            result[identity] = display
    return result


def collect_genres(films: list[dict[str, Any]], *, status: str = "want") -> list[GenreEntry]:
    counts: dict[str, int] = defaultdict(int)
    labels: dict[str, str] = {}
    genreless = 0
    for film in films:
        if film.get("status") != status:
            continue
        genres = film_genres(film)
        if not genres:
            genreless += 1
            continue
        for identity, label in genres.items():
            counts[identity] += 1
            labels.setdefault(identity, label)
    entries = [GenreEntry(identity, labels[identity], count, genre_key(identity)) for identity, count in counts.items()]
    entries.sort(key=lambda entry: (-entry.count, entry.identity or ""))
    if genreless:
        entries.append(GenreEntry(None, "Без жанра", genreless, GENRELESS_KEY))
    return entries


def resolve_genre(films: list[dict[str, Any]], key: str) -> GenreEntry | None:
    if key == GENRELESS_KEY:
        if any(not film_genres(film) for film in films):
            count = sum(1 for film in films if film.get("status") == "want" and not film_genres(film))
            return GenreEntry(None, "Без жанра", count, GENRELESS_KEY)
        return None
    identities: dict[str, str] = {}
    for film in films:
        for identity, label in film_genres(film).items():
            identities.setdefault(identity, label)
    counts = {entry.identity: entry.count for entry in collect_genres(films)}
    matches = [
        GenreEntry(identity, label, counts.get(identity, 0), key)
        for identity, label in identities.items()
        if genre_key(identity) == key
    ]
    return matches[0] if len(matches) == 1 else None


def filter_want_films(films: list[dict[str, Any]], entry: GenreEntry) -> list[dict[str, Any]]:
    result = []
    for film in films:
        if film.get("status") != "want":
            continue
        genres = film_genres(film)
        if (entry.identity is None and not genres) or (entry.identity is not None and entry.identity in genres):
            result.append(film)
    return result


def random_candidates(films: list[dict[str, Any]], entry: GenreEntry | None = None) -> list[dict[str, Any]]:
    if entry is None:
        return [film for film in films if film.get("status") == "want"]
    return filter_want_films(films, entry)


def _emoji(entry: GenreEntry) -> str:
    known = {
        "комедия": "😂", "драма": "🎭", "триллер": "🔪", "фантастика": "🚀",
        "ужасы": "👻", "мелодрама": "💕", "приключения": "🧭", "мультфильм": "🧸",
    }
    return "❔" if entry.identity is None else known.get(entry.identity, "🎞")


def _cb(value: str) -> str:
    if len(value.encode("utf-8")) > 64:
        raise ValueError("Telegram callback_data exceeds 64 bytes")
    return value


def genre_selector_keyboard(entries: list[GenreEntry], page: int, purpose: str) -> InlineKeyboardMarkup:
    page_items, current_page, total_pages = _paginate(entries, page, GENRE_PAGE_SIZE)
    rows = [[InlineKeyboardButton(f"{_emoji(entry)} {entry.label} · {entry.count}", callback_data=_cb(
        f"filmfilter:{'x' if purpose == 'random' else 'l'}:{entry.key}{'' if purpose == 'random' else ':0'}"
    ))] for entry in page_items]
    navigation = []
    if current_page > 0:
        navigation.append(InlineKeyboardButton("⬅️", callback_data=_cb(f"filmfilter:g:{purpose[0]}:{current_page - 1}")))
    if total_pages > 1:
        navigation.append(InlineKeyboardButton(f"{current_page + 1}/{total_pages}", callback_data="noop"))
    if current_page < total_pages - 1:
        navigation.append(InlineKeyboardButton("➡️", callback_data=_cb(f"filmfilter:g:{purpose[0]}:{current_page + 1}")))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton("⬅️ К фильмам", callback_data="menu|films")])
    rows.append([InlineKeyboardButton("🏠 В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def _paginate(items: list[Any], page: int, size: int) -> tuple[list[Any], int, int]:
    total_pages = max(1, (len(items) + size - 1) // size)
    current = max(0, min(page, total_pages - 1))
    return items[current * size:(current + 1) * size], current, total_pages


def filtered_list_keyboard(items: list[dict[str, Any]], entry: GenreEntry, page: int) -> InlineKeyboardMarkup:
    page_items, current_page, total_pages = paginate_items(items, page)
    rows = [[InlineKeyboardButton(film["title"], callback_data=_cb(f"filmfilter:v:{entry.key}:{current_page}:{film['id']}"))] for film in page_items]
    nav = []
    if current_page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=_cb(f"filmfilter:l:{entry.key}:{current_page - 1}")))
    if total_pages > 1:
        nav.append(InlineKeyboardButton(f"{current_page + 1}/{total_pages}", callback_data="noop"))
    if current_page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=_cb(f"filmfilter:l:{entry.key}:{current_page + 1}")))
    if nav:
        rows.append(nav)
    rows.extend([
        [InlineKeyboardButton("🎲 Случайный из жанра", callback_data=_cb(f"filmfilter:x:{entry.key}"))],
        [InlineKeyboardButton("⬅️ К жанрам", callback_data="filmfilter:g:b:0")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])
    return InlineKeyboardMarkup(rows)


def filtered_item_keyboard(film: dict[str, Any], entry: GenreEntry, page: int) -> InlineKeyboardMarkup:
    film_id = film["id"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отметить как просмотренный", callback_data=_cb(f"filmfilter:s:{entry.key}:{page}:{film_id}"))],
        [InlineKeyboardButton("🗑️ Удалить", callback_data=_cb(f"filmfilter:c:{entry.key}:{page}:{film_id}"))],
        [InlineKeyboardButton("⬅️ К списку", callback_data=_cb(f"filmfilter:l:{entry.key}:{page}"))],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])


def random_result_keyboard(film: dict[str, Any], key: str) -> InlineKeyboardMarkup:
    film_id = film["id"]
    genre_mode = key != "any"
    rows = [
        [InlineKeyboardButton("🎲 Другой из этого жанра" if genre_mode else "🎲 Выбрать другой", callback_data=_cb(f"filmfilter:x:{key}"))],
        [InlineKeyboardButton("🎭 Другой жанр" if genre_mode else "🎭 Выбрать жанр", callback_data="filmfilter:g:r:0")],
        [InlineKeyboardButton("✅ Отметить как просмотренный", callback_data=_cb(f"filmfilter:rs:{key}:{film_id}"))],
        [InlineKeyboardButton("🗑️ Удалить", callback_data=_cb(f"filmfilter:rc:{key}:{film_id}"))],
    ]
    rows.append([InlineKeyboardButton("📋 К фильмам этого жанра" if genre_mode else "📋 Все фильмы в планах", callback_data=_cb(
        f"filmfilter:l:{key}:0" if genre_mode else "list|films|want|0"
    ))])
    rows.append([InlineKeyboardButton("🏠 В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


async def film_filter_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    if _safe_edit_message is None or _build_item_text is None:
        raise RuntimeError("Film filter handlers are not configured")
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "r":
        return await _show_random_menu(query)
    if action == "a":
        return await _show_random_result(query, "any")
    if action == "g" and len(parts) == 4:
        return await _show_genres(query, "random" if parts[2] == "r" else "browse", _int(parts[3]))
    if action == "l" and len(parts) == 4:
        return await _show_filtered_list(query, parts[2], _int(parts[3]))
    if action == "v" and len(parts) == 5:
        return await _show_filtered_item(query, parts[2], _int(parts[3]), parts[4])
    if action in {"s", "c", "d"} and len(parts) == 5:
        return await _handle_filtered_mutation(query, action, parts[2], _int(parts[3]), parts[4])
    if action == "x" and len(parts) == 3:
        return await _show_random_result(query, parts[2])
    if action in {"rs", "rc", "rd"} and len(parts) == 4:
        return await _handle_random_mutation(query, action, parts[2], parts[3])
    if action == "fr" and len(parts) == 6:
        return await _handle_filter_reaction(query, update, parts[2], _int(parts[3]), parts[4], parts[5])
    if action == "rr" and len(parts) == 5:
        return await _handle_filter_reaction(query, update, parts[2], -1, parts[3], parts[4])
    return await _show_stale_filter(query)


async def _show_genres(query: Any, purpose: str, page: int) -> int:
    entries = collect_genres(storage.load().get("films", []))
    text = "🎲 Выбрать жанр" if purpose == "random" else "🎭 Жанры"
    text += "\n\nФильмы, которые хотим посмотреть.\nВыберите жанр:"
    if not entries:
        text = "🎭 У фильмов в планах пока не указаны жанры."
    await _safe_edit_message(query, text, reply_markup=genre_selector_keyboard(entries, page, purpose))
    return SECTION


async def _show_filtered_list(
    query: Any, key: str, page: int, notice: str = "", known_entry: GenreEntry | None = None,
) -> int:
    films = storage.load().get("films", [])
    entry = resolve_genre(films, key) or known_entry
    if entry is None:
        return await _show_stale_filter(query)
    items = filter_want_films(films, entry)
    if not items:
        return await _show_empty_genre(query, entry)
    _, current, _ = paginate_items(items, page)
    start = current * PAGE_SIZE + 1
    end = min(len(items), start + PAGE_SIZE - 1)
    prefix = f"{notice}\n\n" if notice else ""
    text = f"{prefix}🎭 {entry.label}\n\n{len(items)} фильма в планах\nПоказаны {start}–{end}."
    await _safe_edit_message(query, text, reply_markup=filtered_list_keyboard(items, entry, current))
    return SECTION


async def _show_filtered_item(query: Any, key: str, page: int, film_id: str) -> int:
    films = storage.load().get("films", [])
    entry = resolve_genre(films, key)
    if entry is None:
        return await _show_stale_filter(query)
    film = find_item(films, film_id)
    if film is None or film not in filter_want_films(films, entry):
        await _safe_edit_message(query, "Фильм уже удалён или список изменился.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Вернуться к жанру", callback_data=_cb(f"filmfilter:l:{key}:{page}"))],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]))
        return SECTION
    await _safe_edit_message(query, _build_item_text("films", film), reply_markup=filtered_item_keyboard(film, entry, page))
    return SECTION


async def _handle_filtered_mutation(query: Any, action: str, key: str, page: int, film_id: str) -> int:
    if action == "c":
        await _safe_edit_message(query, "Точно удалить фильм?", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, удалить", callback_data=_cb(f"filmfilter:d:{key}:{page}:{film_id}"))],
            [InlineKeyboardButton("↩️ Нет, вернуться", callback_data=_cb(f"filmfilter:v:{key}:{page}:{film_id}"))],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]))
        return SECTION
    entry = resolve_genre(storage.load().get("films", []), key)
    if entry is None:
        return await _show_stale_filter(query)
    result = set_film_status(film_id, "watched") if action == "s" else delete_film(film_id)
    if not result.found:
        return await _show_filtered_list(query, key, page, "Фильм уже удалён или список изменился.", entry)
    if action == "s":
        return await _show_filter_reaction_prompt(query, film_id, f"fr:{key}:{page}", f"filmfilter:l:{key}:{page}")
    notice = "Фильм отмечен как просмотренный." if action == "s" else "Фильм удалён."
    return await _show_filtered_list(query, key, page, notice, entry)


async def _show_random_menu(query: Any) -> int:
    await _safe_edit_message(query, "🎲 Что смотрим?", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Любой фильм", callback_data="filmfilter:a")],
        [InlineKeyboardButton("🎭 Выбрать жанр", callback_data="filmfilter:g:r:0")],
        [InlineKeyboardButton("⬅️ К фильмам", callback_data="menu|films")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ]))
    return SECTION


async def _show_random_result(query: Any, key: str) -> int:
    films = storage.load().get("films", [])
    entry = None if key == "any" else resolve_genre(films, key)
    if key != "any" and entry is None:
        return await _show_stale_filter(query)
    candidates = random_candidates(films, entry)
    if not candidates:
        if entry is not None:
            return await _show_empty_genre(query, entry)
        await _safe_edit_message(query, "🎲 Фильмов в планах пока нет.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Добавить фильм", callback_data="add|films")],
            [InlineKeyboardButton("🎬 К фильмам", callback_data="menu|films")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]))
        return SECTION
    film = random.choice(candidates)
    await _safe_edit_message(query, "🎲 Сегодня смотрим:\n\n" + _build_item_text("films", film), reply_markup=random_result_keyboard(film, key))
    return SECTION


async def _handle_random_mutation(query: Any, action: str, key: str, film_id: str) -> int:
    if action == "rc":
        await _safe_edit_message(query, "Точно удалить фильм?", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, удалить", callback_data=_cb(f"filmfilter:rd:{key}:{film_id}"))],
            [InlineKeyboardButton("↩️ Нет, выбрать снова", callback_data=_cb(f"filmfilter:x:{key}"))],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]))
        return SECTION
    entry = resolve_genre(storage.load().get("films", []), key) if key != "any" else None
    if key != "any" and entry is None:
        return await _show_stale_filter(query)
    result = set_film_status(film_id, "watched") if action == "rs" else delete_film(film_id)
    notice = "Фильм отмечен как просмотренный." if action == "rs" else "Фильм удалён."
    if not result.found:
        notice = "Фильм уже удалён или список изменился."
    elif action == "rs":
        return await _show_filter_reaction_prompt(query, film_id, f"rr:{key}", f"filmfilter:x:{key}")
    elif entry is not None and not random_candidates(storage.load().get("films", []), entry):
        return await _show_empty_genre(query, entry)
    genre_mode = key != "any"
    rows = [
        [InlineKeyboardButton("🎲 Выбрать другой из этого жанра" if genre_mode else "🎲 Выбрать другой", callback_data=_cb(f"filmfilter:x:{key}"))],
        [InlineKeyboardButton("📋 К жанру" if genre_mode else "📋 Все фильмы в планах", callback_data=_cb(f"filmfilter:l:{key}:0" if genre_mode else "list|films|want|0"))],
        [InlineKeyboardButton("🎭 Другой жанр" if genre_mode else "🎭 Выбрать жанр", callback_data="filmfilter:g:r:0")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ]
    await _safe_edit_message(query, notice, reply_markup=InlineKeyboardMarkup(rows))
    return SECTION


async def _show_filter_reaction_prompt(query: Any, film_id: str, route: str, back_callback: str) -> int:
    rows = [
        [InlineKeyboardButton("❤️ Понравилось", callback_data=_cb(f"filmfilter:{route}:{film_id}:like"))],
        [InlineKeyboardButton("😐 Нормально", callback_data=_cb(f"filmfilter:{route}:{film_id}:neutral"))],
        [InlineKeyboardButton("👎 Не понравилось", callback_data=_cb(f"filmfilter:{route}:{film_id}:dislike"))],
        [InlineKeyboardButton("Пропустить", callback_data=_cb(back_callback))],
    ]
    await _safe_edit_message(query, "Как тебе фильм?", reply_markup=InlineKeyboardMarkup(rows))
    return SECTION


async def _handle_filter_reaction(
    query: Any, update: Update, key: str, page: int, film_id: str, reaction: str,
) -> int:
    result = set_film_reaction(film_id, get_wishlist_owner_by_user(update), reaction)
    back = f"filmfilter:l:{key}:{page}" if page >= 0 else f"filmfilter:x:{key}"
    if not result.found:
        await _safe_edit_message(query, "Фильм не найден.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Вернуться", callback_data=_cb(back))]
        ]))
        return SECTION
    await _safe_edit_message(query, _build_item_text("films", result.film), reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Продолжить", callback_data=_cb(back))],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ]))
    return SECTION


async def _show_empty_genre(query: Any, entry: GenreEntry) -> int:
    await _safe_edit_message(query, f"В жанре «{entry.label}» больше нет фильмов в планах 👀", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🎭 Выбрать другой жанр", callback_data="filmfilter:g:b:0")],
        [InlineKeyboardButton("🎲 Любой фильм", callback_data="filmfilter:a")],
        [InlineKeyboardButton("🎬 К фильмам", callback_data="menu|films")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ]))
    return SECTION


async def _show_stale_filter(query: Any) -> int:
    await _safe_edit_message(query, "Этот жанр больше недоступен — список фильмов изменился.", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🎭 Открыть актуальные жанры", callback_data="filmfilter:g:b:0")],
        [InlineKeyboardButton("🎬 К фильмам", callback_data="menu|films")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ]))
    return SECTION


def _int(value: str) -> int:
    try:
        return max(0, int(value))
    except ValueError:
        return 0
