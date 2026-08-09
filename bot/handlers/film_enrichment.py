from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.services.film_enrichment import (
    EnrichmentDisposition,
    apply_metadata_atomic,
    classify_search_results,
    identity_state,
    metadata_matches_film,
)
from bot.services.movie_metadata import MediaMetadata, MediaMetadataError, MediaMetadataProvider, MediaSearchResult
from bot.states import (
    FILM_ENRICHMENT_CONFIRMING,
    FILM_ENRICHMENT_MANUAL_QUERY,
    FILM_ENRICHMENT_REVIEW,
    FILM_ENRICHMENT_SELECTING,
    SECTION,
)
from bot.storage import storage
from bot.utils import ensure_access, item_status_label

logger = logging.getLogger(__name__)

SESSION_KEY = "film_enrichment"
QUEUE_NAMES = ("ambiguous", "unmatched", "conflict", "provider_error")
MAX_CONSECUTIVE_ERRORS = 3

_safe_edit_message: Callable[..., Any] | None = None
_metadata_provider: MediaMetadataProvider | None = None
_batch_running = False


@dataclass(slots=True)
class BatchReport:
    total: int = 0
    processed: int = 0
    enriched: int = 0
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    unmatched: list[dict[str, Any]] = field(default_factory=list)
    conflict: list[dict[str, Any]] = field(default_factory=list)
    provider_error: list[dict[str, Any]] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)


def configure_film_enrichment_handlers(*, safe_edit_message: Callable[..., Any], metadata_provider: MediaMetadataProvider | None) -> None:
    global _safe_edit_message, _metadata_provider
    _safe_edit_message = safe_edit_message
    _metadata_provider = metadata_provider


def batch_is_running() -> bool:
    return _batch_running


def claim_batch() -> bool:
    """Claim the process-wide batch slot without yielding to another callback."""
    global _batch_running
    if _batch_running:
        return False
    _batch_running = True
    return True


def release_batch() -> None:
    global _batch_running
    _batch_running = False


def _film(film_id: str) -> dict[str, Any] | None:
    return next((item for item in storage.load().get("films", []) if str(item.get("id")) == str(film_id)), None)


def _queue_item(film: dict[str, Any], candidates: tuple[MediaSearchResult, ...] = (), **extra: Any) -> dict[str, Any]:
    return {"film_id": str(film.get("id") or ""), "candidates": list(candidates), **extra}


async def process_enrichment_batch(
    provider: MediaMetadataProvider,
    *,
    progress: Callable[[BatchReport], Any] | None = None,
    pace_seconds: float = 0.2,
) -> BatchReport:
    films = storage.load().get("films", [])
    pending = [item for item in films if identity_state(item) != "complete"]
    report = BatchReport(total=len(pending))
    consecutive_errors = 0

    for index, snapshot in enumerate(pending):
        film = _film(str(snapshot.get("id") or ""))
        if film is None or identity_state(film) == "complete":
            report.processed += 1
            continue
        if identity_state(film) == "partial":
            report.conflict.append(_queue_item(film, reason="partial_identity"))
            report.processed += 1
            consecutive_errors = 0
        else:
            try:
                results = await provider.search_titles(str(film.get("title") or ""))
                decision = classify_search_results(film, results)
                if decision.disposition is EnrichmentDisposition.AUTOMATIC:
                    selected = decision.candidates[0]
                    metadata = await provider.get_title_details(selected.media_type, selected.external_id)
                    current = _film(str(film.get("id") or ""))
                    if current is None:
                        pass
                    elif (
                        metadata.metadata_provider != selected.metadata_provider
                        or metadata.media_type != selected.media_type
                        or metadata.external_id != selected.external_id
                        or not metadata_matches_film(current, metadata)
                    ):
                        report.ambiguous.append(_queue_item(current, decision.candidates))
                    else:
                        applied = apply_metadata_atomic(storage, str(film.get("id") or ""), metadata)
                        if applied.disposition is EnrichmentDisposition.ENRICHED:
                            report.enriched += 1
                        elif applied.disposition is EnrichmentDisposition.CONFLICT:
                            report.conflict.append(_queue_item(current, decision.candidates, conflicting_film=applied.conflicting_film))
                elif decision.disposition is EnrichmentDisposition.AMBIGUOUS:
                    report.ambiguous.append(_queue_item(film, decision.candidates))
                else:
                    report.unmatched.append(_queue_item(film))
                consecutive_errors = 0
            except MediaMetadataError:
                logger.info("Film enrichment provider failure for film %s", film.get("id"), exc_info=True)
                report.provider_error.append(_queue_item(film))
                consecutive_errors += 1
            report.processed += 1

        if progress is not None and (report.processed == report.total or report.processed % 5 == 0):
            value = progress(report)
            if hasattr(value, "__await__"):
                await value
        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            report.deferred.extend(str(item.get("id") or "") for item in pending[index + 1:])
            break
        if pace_seconds:
            await asyncio.sleep(pace_seconds)
    return report


async def film_enrichment_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    if _safe_edit_message is None:
        raise RuntimeError("Film enrichment handlers are not configured")
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "open":
        return await _show_preflight(query)
    if action == "start":
        if _metadata_provider is None:
            return await _show_preflight(query)
        if not claim_batch():
            await _safe_edit_message(query, "🔄 Обновление уже запущено другим пользователем.", reply_markup=_navigation())
            return SECTION
        try:
            async def progress(report: BatchReport) -> None:
                await _safe_edit_message(query, _progress_text(report))

            report = await process_enrichment_batch(_metadata_provider, progress=progress)
            context.user_data[SESSION_KEY] = _report_session(report)
            await _safe_edit_message(query, _summary_text(report), reply_markup=_summary_keyboard(report))
        finally:
            release_batch()
        return SECTION
    if action == "rescan":
        return await _show_preflight(query)
    if action == "review" and len(parts) > 2 and parts[2] in QUEUE_NAMES:
        session = context.user_data.get(SESSION_KEY)
        if not isinstance(session, dict):
            return await _stale(query)
        session["queue"] = parts[2]
        session["position"] = 0
        return await _show_review(query, session)
    if action == "pick" and len(parts) > 2:
        return await _pick_candidate(query, context, parts[2])
    if action == "back":
        session = context.user_data.get(SESSION_KEY)
        return await _show_review(query, session) if isinstance(session, dict) else await _stale(query)
    if action == "manual":
        session = context.user_data.get(SESSION_KEY)
        item = _current_item(session)
        film = _film(str((item or {}).get("film_id") or ""))
        if film is None:
            return await _stale(query)
        await _safe_edit_message(query, f"🔎 Ручной поиск\n\nОбновляем существующий фильм:\n🎬 {film['title']}\n\nОтправьте другое название для поиска.\nНазвание в списке не изменится.")
        return FILM_ENRICHMENT_MANUAL_QUERY
    if action == "confirm":
        return await _confirm(query, context)
    if action == "open_conflict":
        session = context.user_data.get(SESSION_KEY)
        item = _current_item(session)
        conflict = (item or {}).get("conflicting_film")
        if not isinstance(conflict, dict):
            return await _stale(query)
        text = f"🎬 {conflict.get('title', 'Без названия')}"
        if conflict.get("year"):
            text += f"\n{conflict['year']}"
        text += f"\nСтатус: {item_status_label('films', conflict.get('status', 'want'))}"
        await _safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ К конфликту", callback_data="filmenrich:back")],
            *_navigation().inline_keyboard,
        ]))
        return FILM_ENRICHMENT_REVIEW
    if action in {"skip", "next"}:
        session = context.user_data.get(SESSION_KEY)
        if not isinstance(session, dict):
            return await _stale(query)
        session["position"] = int(session.get("position", 0)) + 1
        session.pop("candidate", None)
        return await _show_review(query, session)
    return await _stale(query)


async def film_enrichment_manual_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    session = context.user_data.get(SESSION_KEY)
    item = _current_item(session)
    film = _film(str((item or {}).get("film_id") or ""))
    if film is None or _metadata_provider is None:
        await update.message.reply_text("Запрос устарел. Запустите проверку заново.")
        return SECTION
    manual_query = (update.message.text or "").strip()
    if not manual_query:
        await update.message.reply_text("Запрос не должен быть пустым.")
        return FILM_ENRICHMENT_MANUAL_QUERY
    try:
        results = await _metadata_provider.search_titles(manual_query)
    except MediaMetadataError:
        await update.message.reply_text("Не удалось связаться с TMDB. Повторите позже.")
        return FILM_ENRICHMENT_MANUAL_QUERY
    item["candidates"] = list(results)
    session["manual_query"] = manual_query
    await update.message.reply_text("Что это за фильм или сериал?", reply_markup=_candidate_keyboard(results))
    return FILM_ENRICHMENT_SELECTING


async def _show_preflight(query: Any) -> int:
    films = storage.load().get("films", [])
    missing = sum(identity_state(item) != "complete" for item in films)
    complete = sum(identity_state(item) == "complete" for item in films)
    text = (
        "🎬 Обновление данных фильмов и сериалов из TMDB\n\n"
        f"Без привязки к TMDB: {missing}\nУже с данными: {complete}\n\n"
        "Будут обновлены только метаданные.\n"
        "Ваши названия, статусы и комментарии останутся без изменений."
    )
    rows = []
    if _metadata_provider is not None and missing:
        rows.append([InlineKeyboardButton("🚀 Начать безопасное обновление", callback_data="filmenrich:start")])
    elif _metadata_provider is None:
        text += "\n\n⚠️ Поиск TMDB сейчас не настроен."
    rows.extend(_navigation().inline_keyboard)
    await _safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(rows))
    return SECTION


def _report_session(report: BatchReport) -> dict[str, Any]:
    return {name: getattr(report, name) for name in QUEUE_NAMES} | {"queue": "", "position": 0}


def _progress_text(report: BatchReport) -> str:
    return (
        f"🔄 Обновляю данные фильмов…\n\n{report.processed} / {report.total}\n\n"
        f"✅ Обновлено: {report.enriched}\n❓ На проверку: {len(report.ambiguous)}\n"
        f"⏭ Не найдено: {len(report.unmatched)}\n⚠️ Конфликты: {len(report.conflict)}\n"
        f"🌐 Ошибки TMDB: {len(report.provider_error)}"
    )


def _summary_text(report: BatchReport) -> str:
    text = (
        f"🔄 Обновление завершено\n\nПроверено: {report.processed}\n"
        f"✅ Обновлено автоматически: {report.enriched}\n"
        f"❓ Требуют проверки: {len(report.ambiguous)}\n"
        f"⏭ Не найдено: {len(report.unmatched)}\n"
        f"⚠️ Возможные дубли: {len(report.conflict)}\n"
        f"🌐 Ошибки TMDB: {len(report.provider_error)}"
    )
    if report.deferred:
        text += f"\n⏸ Отложено из-за сбоя TMDB: {len(report.deferred)}"
    return text + "\n\nОшибки TMDB не считаются ненайденными."


def _summary_keyboard(report: BatchReport) -> InlineKeyboardMarkup:
    rows = []
    labels = {
        "ambiguous": "❓ Проверить сомнительные",
        "unmatched": "🔎 Проверить ненайденные",
        "conflict": "⚠️ Посмотреть конфликты",
        "provider_error": "🌐 Повторить ошибки TMDB",
    }
    for name in QUEUE_NAMES:
        if getattr(report, name):
            rows.append([InlineKeyboardButton(labels[name], callback_data=f"filmenrich:review:{name}")])
    rows.append([InlineKeyboardButton("🔄 Пересканировать оставшиеся", callback_data="filmenrich:rescan")])
    rows.extend(_navigation().inline_keyboard)
    return InlineKeyboardMarkup(rows)


def _current_item(session: Any) -> dict[str, Any] | None:
    if not isinstance(session, dict):
        return None
    queue = session.get(session.get("queue"))
    position = session.get("position", 0)
    if isinstance(queue, list) and isinstance(position, int) and 0 <= position < len(queue):
        return queue[position]
    return None


async def _show_review(query: Any, session: dict[str, Any]) -> int:
    item = _current_item(session)
    if item is None:
        await _safe_edit_message(query, "В этой очереди больше нет фильмов.", reply_markup=_navigation(rescan=True))
        return SECTION
    film = _film(str(item.get("film_id") or ""))
    if film is None or identity_state(film) == "complete":
        session["position"] += 1
        return await _show_review(query, session)
    queue = session[session["queue"]]
    pos = session["position"]
    text = f"❓ Требуется проверка\n{pos + 1} из {len(queue)}\n\nСейчас в списке:\n🎬 {film['title']}"
    if session["queue"] == "conflict":
        conflict = item.get("conflicting_film") or {}
        if conflict:
            text += f"\n\n⚠️ TMDB-фильм уже привязан к:\n🎬 {conflict.get('title', 'Без названия')}"
    keyboard = _candidate_keyboard(item.get("candidates", []))
    if session["queue"] == "conflict" and item.get("conflicting_film"):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👀 Открыть существующий", callback_data="filmenrich:open_conflict")],
            *keyboard.inline_keyboard,
        ])
    await _safe_edit_message(query, text + "\n\nЧто это за фильм?", reply_markup=keyboard)
    return FILM_ENRICHMENT_REVIEW


def _candidate_keyboard(results: list[MediaSearchResult]) -> InlineKeyboardMarkup:
    rows = []
    for index, result in enumerate(results[:8]):
        icon = "📺" if result.media_type == "tv" else "🎬"
        label = f"{icon} {result.title} · {result.year}" if result.year else f"{icon} {result.title}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"filmenrich:pick:{index}")])
    rows.extend([
        [InlineKeyboardButton("🔎 Искать вручную", callback_data="filmenrich:manual")],
        [InlineKeyboardButton("⏭ Пропустить", callback_data="filmenrich:skip")],
        [InlineKeyboardButton("🎬 К фильмам", callback_data="menu|films")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])
    return InlineKeyboardMarkup(rows)


async def _pick_candidate(query: Any, context: ContextTypes.DEFAULT_TYPE, raw_index: str) -> int:
    session = context.user_data.get(SESSION_KEY)
    item = _current_item(session)
    try:
        index = int(raw_index)
        result = item["candidates"][index]
    except (TypeError, ValueError, IndexError, KeyError):
        return await _stale(query)
    if not isinstance(result, MediaSearchResult) or _metadata_provider is None:
        return await _stale(query)
    try:
        metadata = await _metadata_provider.get_title_details(result.media_type, result.external_id)
    except MediaMetadataError:
        await _safe_edit_message(query, "🌐 Не удалось загрузить данные TMDB.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К вариантам", callback_data="filmenrich:back")]]))
        return FILM_ENRICHMENT_REVIEW
    session["candidate"] = metadata
    film = _film(str(item.get("film_id") or ""))
    if film is None:
        return await _stale(query)
    await _safe_edit_message(query, _preview(film, metadata), reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Это он — обновить", callback_data="filmenrich:confirm")],
        [InlineKeyboardButton("⬅️ Другие варианты", callback_data="filmenrich:back")],
        [InlineKeyboardButton("⏭ Пропустить", callback_data="filmenrich:skip")],
    ]))
    return FILM_ENRICHMENT_CONFIRMING


async def _confirm(query: Any, context: ContextTypes.DEFAULT_TYPE) -> int:
    session = context.user_data.get(SESSION_KEY)
    item = _current_item(session)
    metadata = session.get("candidate") if isinstance(session, dict) else None
    if item is None or not isinstance(metadata, MediaMetadata):
        return await _stale(query)
    result = apply_metadata_atomic(storage, str(item.get("film_id") or ""), metadata)
    if result.disposition is EnrichmentDisposition.ENRICHED:
        session.pop("candidate", None)
        await _safe_edit_message(query, f"✅ Данные обновлены.\n\n🎬 {(result.film or {}).get('title', '')}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Следующий", callback_data="filmenrich:next")], *_navigation().inline_keyboard]))
        return FILM_ENRICHMENT_REVIEW
    if result.disposition is EnrichmentDisposition.CONFLICT:
        await _safe_edit_message(query, "⚠️ Этот TMDB-фильм уже привязан к другой записи. Ничего не изменено.", reply_markup=_navigation(rescan=True))
        return SECTION
    return await _stale(query)


def _preview(film: dict[str, Any], metadata: MediaMetadata) -> str:
    icon = "📺" if metadata.media_type == "tv" else "🎬"
    type_label = "Сериал" if metadata.media_type == "tv" else "Фильм"
    lines = [f"🎬 В вашем списке:\n{film['title']}", "", f"Найдено в TMDB:\n{icon} {metadata.title}"]
    facts = [type_label] + ([str(metadata.year)] if metadata.year else [])
    if facts:
        lines.append(" · ".join(facts))
    if metadata.genres:
        lines.append(" · ".join(metadata.genres))
    if metadata.external_rating is not None:
        lines.append(f"⭐ {metadata.external_rating:g}")
    if metadata.description:
        lines.extend(["", metadata.description])
    return "\n".join(lines)


async def _stale(query: Any) -> int:
    await _safe_edit_message(query, "Запрос устарел или фильм уже обновлён.", reply_markup=_navigation(rescan=True))
    return SECTION


def _navigation(*, rescan: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if rescan:
        rows.append([InlineKeyboardButton("🔄 Пересканировать", callback_data="filmenrich:rescan")])
    rows.extend([
        [InlineKeyboardButton("🎬 К фильмам", callback_data="menu|films")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])
    return InlineKeyboardMarkup(rows)
