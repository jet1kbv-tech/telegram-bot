from __future__ import annotations

import logging
import random
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.services.film_duplicates import DuplicateKind, DuplicateResult, find_movie_duplicate, normalize_movie_title
from bot.services.movie_metadata import (
    MEDIA_TYPE_MOVIE,
    MediaMetadata,
    MediaMetadataError,
    MediaMetadataProvider,
    MediaSearchResult,
)
from bot.states import ADDING_FILM_COMMENT, ADDING_FILM_TITLE, CONFIRMING_FILM_ADD, SECTION, SELECTING_FILM_METADATA
from bot.storage import make_id, storage
from bot.utils import ensure_access, get_user_name, item_status_label

logger = logging.getLogger(__name__)

_safe_edit_message: Callable[..., Any] | None = None
_build_item_text: Callable[[str, dict[str, Any]], str] | None = None
_item_keyboard: Callable[..., Any] | None = None
_main_menu_keyboard: Callable[[], Any] | None = None
_metadata_provider: MediaMetadataProvider | None = None

FILM_DRAFT_KEY = "film_add_draft"
FILM_CONVERSATION_KEYS = (
    FILM_DRAFT_KEY,
    "film_title",
    "film_comment",
    "film_rating_item_id",
    "film_rating_page",
    "film_rating_status_filter",
    "pending_sasha_rating",
)


def clear_film_conversation_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Discard transient Films input while leaving navigation context intact."""
    for key in FILM_CONVERSATION_KEYS:
        context.user_data.pop(key, None)


def configure_films_handlers(
    *,
    safe_edit_message: Callable[..., Any],
    build_item_text: Callable[[str, dict[str, Any]], str],
    item_keyboard: Callable[..., Any],
    main_menu_keyboard: Callable[[], Any],
    metadata_provider: MediaMetadataProvider | None = None,
) -> None:
    global _safe_edit_message, _build_item_text, _item_keyboard, _main_menu_keyboard, _metadata_provider
    _safe_edit_message = safe_edit_message
    _build_item_text = build_item_text
    _item_keyboard = item_keyboard
    _main_menu_keyboard = main_menu_keyboard
    _metadata_provider = metadata_provider


def _ensure_configured() -> None:
    if _safe_edit_message is None or _build_item_text is None or _item_keyboard is None or _main_menu_keyboard is None:
        raise RuntimeError("Films handlers are not configured")


async def show_random_film(update: Update) -> int:
    _ensure_configured()
    query = update.callback_query
    data = storage.load()
    unwatched = [item for item in data.get("films", []) if item.get("status") == "want"]
    if not unwatched:
        await _safe_edit_message(
            query,
            "🎲 Непросмотренных фильмов пока нет.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить фильм или сериал", callback_data="add|films")],
                [InlineKeyboardButton("⬅️ Назад к фильмам", callback_data="menu|films")],
            ]),
        )
        return SECTION

    film = random.choice(unwatched)
    await _safe_edit_message(
        query,
        "🎲 Случайный выбор из непросмотренных:\n\n" + _build_item_text("films", film),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎲 Выбрать ещё", callback_data="random|films")],
            [InlineKeyboardButton("📋 Все непросмотренные", callback_data="list|films|want|0")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]),
    )
    return SECTION


async def add_film_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Название фильма не должно быть пустым. Попробуй ещё раз:")
        return ADDING_FILM_TITLE

    draft = {"query": title, "results": [], "candidate": None, "comment": "", "possible_duplicate_id": ""}
    context.user_data[FILM_DRAFT_KEY] = draft
    if _metadata_provider is None:
        await update.message.reply_text(
            "🔎 Поиск фильмов и сериалов сейчас не настроен. Можно добавить запись только по названию.",
            reply_markup=_manual_fallback_keyboard(),
        )
        return SELECTING_FILM_METADATA

    try:
        raw_results = await _metadata_provider.search_titles(title)
        ordered_results = _ordered_results(title, raw_results)
        results = ordered_results[:8]
        search_complete = getattr(raw_results, "complete", True)
    except MediaMetadataError:
        logger.info("Movie metadata search is unavailable", exc_info=True)
        await update.message.reply_text(
            "Не удалось связаться с сервисом фильмов и сериалов. Можно повторить поиск или добавить запись только по названию.",
            reply_markup=_manual_fallback_keyboard(),
        )
        return SELECTING_FILM_METADATA

    draft["results"] = results
    draft["search_complete"] = search_complete
    if not results:
        await update.message.reply_text(
            "Ничего не найдено. Попробуй другое название или добавь фильм только по названию.",
            reply_markup=_manual_fallback_keyboard(),
        )
        return SELECTING_FILM_METADATA

    exact_matches = [result for result in ordered_results if _is_exact_result(title, result)]
    if search_complete and len(exact_matches) == 1:
        selected = results.index(exact_matches[0])
        return await _select_result(update, context, selected, from_message=True)

    await update.message.reply_text("Что именно добавить?", reply_markup=_results_keyboard(results))
    return SELECTING_FILM_METADATA


async def add_film_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    draft = context.user_data.get(FILM_DRAFT_KEY)
    if not isinstance(draft, dict) or not isinstance(draft.get("candidate"), MediaMetadata):
        await update.message.reply_text("Поиск устарел. Начни добавление фильма заново.")
        return SECTION
    comment = (update.message.text or "").strip()
    draft["comment"] = "" if comment == "-" else comment
    await update.message.reply_text(_preview_text(draft["candidate"], draft["comment"]), reply_markup=_preview_keyboard(draft))
    return CONFIRMING_FILM_ADD


async def film_metadata_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _ensure_configured()
    if not await ensure_access(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    draft = context.user_data.get(FILM_DRAFT_KEY)

    if action == "cancel":
        clear_film_conversation_data(context)
        context.user_data["active_section"] = "films"
        await _safe_edit_message(query, "Добавление фильма отменено.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 К фильмам", callback_data="menu|films")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]))
        return SECTION

    if action == "search_again":
        clear_film_conversation_data(context)
        await _safe_edit_message(query, "Введите название фильма или сериала:")
        return ADDING_FILM_TITLE

    if not isinstance(draft, dict):
        await _safe_edit_message(query, "Поиск устарел. Начни добавление фильма заново.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Добавить фильм или сериал", callback_data="add|films")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]))
        return SECTION

    if action == "manual":
        draft["candidate"] = MediaMetadata.manual(str(draft.get("query") or "Без названия"))
        draft["possible_duplicate_id"] = ""
        return await _show_candidate(query, draft)

    if action == "select" and len(parts) == 3:
        try:
            index = int(parts[2])
        except ValueError:
            return await _show_stale(query)
        return await _select_result(update, context, index)

    if action == "results":
        results = draft.get("results")
        if not isinstance(results, list) or not results:
            return await _show_stale(query)
        await _safe_edit_message(query, "Что именно добавить?", reply_markup=_results_keyboard(results))
        return SELECTING_FILM_METADATA

    candidate = draft.get("candidate")
    if not isinstance(candidate, MediaMetadata):
        return await _show_stale(query)

    if action == "comment":
        await _safe_edit_message(query, "Отправь комментарий к фильму одним сообщением. Если не нужен, напиши -")
        return ADDING_FILM_COMMENT

    if action == "preview":
        await _safe_edit_message(query, _preview_text(candidate, str(draft.get("comment") or "")), reply_markup=_preview_keyboard(draft))
        return CONFIRMING_FILM_ADD

    if action == "force" and len(parts) == 3:
        duplicate = find_movie_duplicate(candidate, storage.load().get("films", []))
        if duplicate.kind is DuplicateKind.DEFINITIVE:
            return await _show_duplicate(query, duplicate, definitive=True)
        if duplicate.kind is DuplicateKind.POSSIBLE and duplicate.matching_film:
            if duplicate.matching_film.get("id") != parts[2]:
                return await _show_possible_duplicate(query, duplicate)
            draft["possible_duplicate_id"] = str(duplicate.matching_film.get("id") or "")
        else:
            draft["possible_duplicate_id"] = ""
        await _safe_edit_message(query, _preview_text(candidate, str(draft.get("comment") or "")), reply_markup=_preview_keyboard(draft))
        return CONFIRMING_FILM_ADD

    if action == "open" and len(parts) == 3:
        film = next((item for item in storage.load().get("films", []) if item.get("id") == parts[2]), None)
        if film is None:
            return await _show_stale(query)
        await _safe_edit_message(query, _build_item_text("films", film), reply_markup=_item_keyboard("films", film, 0, status_filter=film.get("status", "want")))
        return SECTION

    if action == "save":
        return await _save_candidate(update, context, draft, candidate)

    return await _show_stale(query)


async def _select_result(update: Update, context: ContextTypes.DEFAULT_TYPE, index: int, *, from_message: bool = False) -> int:
    draft = context.user_data.get(FILM_DRAFT_KEY)
    results = draft.get("results") if isinstance(draft, dict) else None
    if not isinstance(results, list) or not 0 <= index < len(results) or not isinstance(results[index], MediaSearchResult):
        if from_message:
            await update.message.reply_text("Результат поиска устарел. Попробуй поиск снова.")
            return ADDING_FILM_TITLE
        return await _show_stale(update.callback_query)
    if _metadata_provider is None:
        return await _provider_failure(update, from_message)
    try:
        candidate = await _metadata_provider.get_title_details(results[index].media_type, results[index].external_id)
    except MediaMetadataError:
        logger.info("Movie metadata details are unavailable", exc_info=True)
        return await _provider_failure(update, from_message)
    draft["candidate"] = candidate
    draft["possible_duplicate_id"] = ""
    target = update.message if from_message else update.callback_query
    return await _show_candidate(target, draft, edit=not from_message)


async def _provider_failure(update: Update, from_message: bool) -> int:
    text = "Не удалось загрузить данные фильма. Выбери другой результат или добавь фильм только по названию."
    keyboard = _manual_fallback_keyboard(include_results=True)
    if from_message:
        await update.message.reply_text(text, reply_markup=keyboard)
    else:
        await _safe_edit_message(update.callback_query, text, reply_markup=keyboard)
    return SELECTING_FILM_METADATA


async def _show_candidate(target: Any, draft: dict[str, Any], *, edit: bool = True) -> int:
    candidate = draft["candidate"]
    duplicate = find_movie_duplicate(candidate, storage.load().get("films", []))
    if duplicate.kind is DuplicateKind.DEFINITIVE:
        return await _show_duplicate(target, duplicate, definitive=True, edit=edit)
    if duplicate.kind is DuplicateKind.POSSIBLE:
        return await _show_possible_duplicate(target, duplicate, edit=edit)
    text = _preview_text(candidate, str(draft.get("comment") or ""))
    if edit:
        await _safe_edit_message(target, text, reply_markup=_preview_keyboard(draft))
    else:
        await target.reply_text(text, reply_markup=_preview_keyboard(draft))
    return CONFIRMING_FILM_ADD


async def _save_candidate(update: Update, context: ContextTypes.DEFAULT_TYPE, draft: dict[str, Any], candidate: MediaMetadata) -> int:
    query = update.callback_query
    acknowledged_id = str(draft.get("possible_duplicate_id") or "")
    added_by = get_user_name(update)
    comment = str(draft.get("comment") or "")

    def mutator(data: dict[str, Any]):
        duplicate = find_movie_duplicate(candidate, data.get("films", []))
        if duplicate.kind is DuplicateKind.DEFINITIVE:
            return None, duplicate
        if duplicate.kind is DuplicateKind.POSSIBLE:
            match_id = str((duplicate.matching_film or {}).get("id") or "")
            if not acknowledged_id or acknowledged_id != match_id:
                return None, duplicate
        item = _candidate_to_item(candidate, title=str(draft.get("query") or candidate.title), comment=comment, added_by=added_by)
        data.setdefault("films", []).append(item)
        return item, duplicate

    (item, duplicate), _ = storage.update(mutator)
    if item is None:
        if duplicate.kind is DuplicateKind.DEFINITIVE:
            return await _show_duplicate(query, duplicate, definitive=True)
        return await _show_possible_duplicate(query, duplicate)

    clear_film_conversation_data(context)
    context.user_data["active_section"] = "films"
    await _safe_edit_message(
        query,
        f"Сохранено:\n\n{_build_item_text('films', item)}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Добавить ещё", callback_data="add|films")],
            [InlineKeyboardButton("🎬 К фильмам", callback_data="menu|films")],
            [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
        ]),
    )
    return SECTION


def _candidate_to_item(candidate: MediaMetadata, *, title: str | None = None, comment: str, added_by: str) -> dict[str, Any]:
    return {
        "id": make_id(),
        "title": title or candidate.title,
        "status": "want",
        "added_by": added_by,
        "comment": comment,
        "sasha_rating": None,
        "vova_rating": None,
        "legacy_rating": None,
        "metadata_provider": candidate.metadata_provider,
        "media_type": candidate.media_type,
        "external_id": candidate.external_id,
        "localized_title": candidate.title if candidate.external_id else "",
        "original_title": candidate.original_title,
        "year": candidate.year,
        "genres": list(candidate.genres),
        "description": candidate.description,
        "external_rating": candidate.external_rating,
    }


def _preview_text(candidate: MediaMetadata, comment: str = "") -> str:
    icon, type_label = _media_display(candidate.media_type)
    lines = [f"{icon} {candidate.title}"]
    if candidate.original_title and normalize_movie_title(candidate.original_title) != normalize_movie_title(candidate.title):
        lines.append(candidate.original_title)
    facts = [type_label] if type_label else []
    if candidate.year is not None:
        facts.append(str(candidate.year))
    facts.extend(candidate.genres)
    if facts:
        lines.extend(["", " · ".join(facts)])
    if candidate.external_rating is not None:
        lines.append(f"⭐ {candidate.external_rating:g}")
    if candidate.description:
        lines.extend(["", candidate.description])
    if not candidate.external_id:
        lines.extend(["", "Метаданные не найдены. Фильм будет добавлен только по названию."])
    if comment:
        lines.extend(["", f"Комментарий: {comment}"])
    return "\n".join(lines)


def _results_keyboard(results: list[MediaSearchResult]) -> InlineKeyboardMarkup:
    rows = []
    for index, result in enumerate(results[:8]):
        icon, _ = _media_display(result.media_type)
        label = f"{icon} {result.title} · {result.year}" if result.year else f"{icon} {result.title}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"filmmeta:select:{index}")])
    rows.extend([
        [InlineKeyboardButton("🔎 Искать снова", callback_data="filmmeta:search_again")],
        [InlineKeyboardButton("➕ Добавить только по названию", callback_data="filmmeta:manual")],
        [InlineKeyboardButton("❌ Отмена", callback_data="filmmeta:cancel")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])
    return InlineKeyboardMarkup(rows)


def _is_exact_result(query: str, result: MediaSearchResult) -> bool:
    normalized = normalize_movie_title(query)
    return normalized in {
        normalize_movie_title(result.title),
        normalize_movie_title(result.original_title),
    }


def _ordered_results(query: str, results: list[MediaSearchResult]) -> list[MediaSearchResult]:
    indexed = list(enumerate(results))
    indexed.sort(key=lambda item: (
        0 if _is_exact_result(query, item[1]) else 1,
        item[0],
        0 if item[1].media_type == MEDIA_TYPE_MOVIE else 1,
        normalize_movie_title(item[1].title),
        item[1].external_id,
    ))
    return [result for _, result in indexed]


def _media_display(media_type: str) -> tuple[str, str]:
    if media_type == "movie":
        return "🎬", "Фильм"
    if media_type == "tv":
        return "📺", "Сериал"
    return "🎬", ""


def _manual_fallback_keyboard(*, include_results: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if include_results:
        rows.append([InlineKeyboardButton("🔎 Выбрать другой", callback_data="filmmeta:results")])
    rows.extend([
        [InlineKeyboardButton("🔎 Искать снова", callback_data="filmmeta:search_again")],
        [InlineKeyboardButton("➕ Добавить только по названию", callback_data="filmmeta:manual")],
        [InlineKeyboardButton("❌ Отмена", callback_data="filmmeta:cancel")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])
    return InlineKeyboardMarkup(rows)


def _preview_keyboard(draft: dict[str, Any]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("💬 Добавить комментарий", callback_data="filmmeta:comment")],
        [InlineKeyboardButton("✅ Добавить", callback_data="filmmeta:save")],
    ]
    if draft.get("results"):
        rows.append([InlineKeyboardButton("🔎 Выбрать другой", callback_data="filmmeta:results")])
    rows.extend([
        [InlineKeyboardButton("✏️ Добавить только по названию", callback_data="filmmeta:manual")],
        [InlineKeyboardButton("❌ Отмена", callback_data="filmmeta:cancel")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])
    return InlineKeyboardMarkup(rows)


async def _show_duplicate(target: Any, duplicate: DuplicateResult, *, definitive: bool, edit: bool = True) -> int:
    film = duplicate.matching_film or {}
    lines = ["⚠️ Этот фильм уже есть в вашем списке", "", f"🎬 {film.get('title', 'Без названия')}"]
    if film.get("year"):
        lines.append(str(film["year"]))
    lines.append(f"Статус: {item_status_label('films', film.get('status', 'want'))}")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👀 Открыть фильм", callback_data=f"filmmeta:open:{film.get('id', '')}")],
        [InlineKeyboardButton("🎬 К фильмам", callback_data="menu|films")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])
    if edit:
        await _safe_edit_message(target, "\n".join(lines), reply_markup=keyboard)
    else:
        await target.reply_text("\n".join(lines), reply_markup=keyboard)
    return CONFIRMING_FILM_ADD


async def _show_possible_duplicate(target: Any, duplicate: DuplicateResult, *, edit: bool = True) -> int:
    film = duplicate.matching_film or {}
    text = (
        "⚠️ Похоже, такой фильм уже есть в списке:\n\n"
        f"🎬 {film.get('title', 'Без названия')}\n"
        f"Статус: {item_status_label('films', film.get('status', 'want'))}\n\n"
        "Добавить всё равно?"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👀 Открыть существующий", callback_data=f"filmmeta:open:{film.get('id', '')}")],
        [InlineKeyboardButton("➕ Всё равно добавить", callback_data=f"filmmeta:force:{film.get('id', '')}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="filmmeta:preview")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])
    if edit:
        await _safe_edit_message(target, text, reply_markup=keyboard)
    else:
        await target.reply_text(text, reply_markup=keyboard)
    return CONFIRMING_FILM_ADD


async def _show_stale(query: Any) -> int:
    await _safe_edit_message(query, "Поиск устарел. Начни добавление фильма заново.", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить фильм или сериал", callback_data="add|films")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ]))
    return SECTION
