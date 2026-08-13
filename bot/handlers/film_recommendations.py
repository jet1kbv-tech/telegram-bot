"""Telegram carousel for deterministic movie recommendations."""
from __future__ import annotations

import secrets
import time
from dataclasses import asdict
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.services.film_catalog import create_want_film, stored_film_to_candidate
from bot.services.film_recommendations import (CandidateScore, RecommendationConstraints,
    profiles_for_actor, rank_candidates)
from bot.services.movie_recommendation_service import MovieRecommendationService, RecommendationUnavailable
from bot.states import SECTION
from bot.storage import storage
from bot.utils import ensure_access, get_user_name, get_wishlist_owner_by_user

SESSION_KEY = "film_recommendation_session"
SESSION_TTL = 15 * 60
RESULT_LIMIT = 3
GENRE_LABELS = {"comedy": "Комедия", "drama": "Драма", "horror": "Ужасы", "thriller": "Триллер",
                "science_fiction": "Фантастика", "romance": "Мелодрама", "animation": "Анимация",
                "action": "Боевик", "adventure": "Приключения", "fantasy": "Фэнтези", "mystery": "Детектив",
                "crime": "Криминал", "documentary": "Документальный", "family": "Семейный"}
_service: MovieRecommendationService | None = None
_safe_edit: Callable[..., Any] | None = None
_build_item: Callable[..., str] | None = None
_item_keyboard: Callable[..., Any] | None = None


def configure_film_recommendations(*, service: MovieRecommendationService | None, safe_edit_message,
                                   build_item_text, item_keyboard) -> None:
    global _service, _safe_edit, _build_item, _item_keyboard
    _service, _safe_edit, _build_item, _item_keyboard = service, safe_edit_message, build_item_text, item_keyboard


def clear_recommendation_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(SESSION_KEY, None)


def _token() -> str: return secrets.token_urlsafe(6)


def recommendation_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👫 Нам вдвоём", callback_data="filmrec:actor:both")],
        [InlineKeyboardButton("👤 Мне", callback_data="filmrec:actor:self")],
        [InlineKeyboardButton("📚 Из нашего списка", callback_data="filmrec:want")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")],
    ])


async def _edit(query, text: str, keyboard=None):
    await _safe_edit(query, text, reply_markup=keyboard)


async def _stale(query) -> int:
    await _edit(query, "Эта подборка уже устарела. Давай соберём новую.", InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Новая подборка", callback_data="filmrec:start")],
        [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]))
    return SECTION


def _session(context, token: str | None = None) -> dict[str, Any] | None:
    value = context.user_data.get(SESSION_KEY)
    if not isinstance(value, dict) or value.get("expires", 0) < time.time() or (token and value.get("id") != token):
        clear_recommendation_session(context); return None
    return value


async def show_recommendation_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_recommendation_session(context)
    await _edit(update.callback_query, "✨ Что посмотрим?", recommendation_menu_keyboard())
    return SECTION


def _type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎬 Фильм", callback_data="filmrec:type:movie")],
        [InlineKeyboardButton("📺 Сериал", callback_data="filmrec:type:tv")],
        [InlineKeyboardButton("🎲 Неважно", callback_data="filmrec:type:any")],
        [InlineKeyboardButton("❌ Закрыть", callback_data="filmrec:close")]])


def _constraints(args: dict[str, Any] | None = None) -> RecommendationConstraints:
    args = args or {}
    return RecommendationConstraints(media_type=args.get("media_type") or "any",
        include_genres=frozenset(args.get("include_genres") or ()), exclude_genres=frozenset(args.get("exclude_genres") or ()),
        min_year=args.get("min_year"), max_year=args.get("max_year"), min_rating=args.get("min_rating"),
        max_runtime=args.get("max_runtime"), language=args.get("language") or "", country=args.get("country") or "",
        limit=RESULT_LIMIT)


async def start_from_nl(update: Update, context: ContextTypes.DEFAULT_TYPE, args: dict[str, Any], response: Any) -> int:
    actor = args.get("actor") or "self"
    if actor == "self": actor = get_wishlist_owner_by_user(update)
    if actor not in {"vova", "sasha", "both"}: actor = get_wishlist_owner_by_user(update)
    source = args.get("source") or "external"
    return await _run(update, context, actor, source, _constraints(args), response=response)


async def _run(update: Update, context: ContextTypes.DEFAULT_TYPE, actor: str, source: str,
               constraints: RecommendationConstraints, response: Any | None = None,
               shown: set[tuple[str, str, str]] | None = None, generation: int = 0) -> int:
    films = storage.load().get("films", [])
    try:
        if source == "want":
            candidates = [c for f in films if f.get("status") == "want" if (c := stored_film_to_candidate(f))]
            local = RecommendationConstraints(**{**asdict(constraints), "exclude_want": False, "min_vote_count": 0})
            # Current Want candidates do not boost themselves: this mode uses
            # explicit watched reactions only.
            scores = rank_candidates(candidates, profiles_for_actor(films, actor, include_want=False), (), (), local)
        else:
            if _service is None: raise RecommendationUnavailable()
            scores = await _service.recommend(films, actor=actor, constraints=constraints,
                                              shown=shown, generation=generation)
    except Exception:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Попробовать снова", callback_data="filmrec:retry")],
            [InlineKeyboardButton("📚 Из нашего списка", callback_data="filmrec:want")],
            [InlineKeyboardButton("❌ Закрыть", callback_data="filmrec:close")]])
        text = "Сейчас не получилось собрать подборку. Попробуй ещё раз чуть позже."
        if response: await response.reply_text(text, reply_markup=keyboard)
        else: await _edit(update.callback_query, text, keyboard)
        return SECTION
    selected = scores[:RESULT_LIMIT]
    all_shown = set(shown or ())
    all_shown.update((score.candidate.provider, score.candidate.media_type, score.candidate.external_id) for score in selected)
    session = {"id": _token(), "actor": actor, "source": source, "constraints": constraints,
               "scores": selected, "index": 0, "shown": all_shown, "generation": generation,
               "expires": time.time() + SESSION_TTL}
    context.user_data[SESSION_KEY] = session
    if not scores:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Ослабить фильтры", callback_data=f"filmrec:relax:{session['id']}")],
            [InlineKeyboardButton("✨ Новая подборка", callback_data="filmrec:start")],
            [InlineKeyboardButton("📚 Из нашего списка", callback_data="filmrec:want")],
            [InlineKeyboardButton("❌ Закрыть", callback_data="filmrec:close")]])
        text = ("Других подходящих вариантов пока не нашлось." if source == "external" and shown else
                "По этим условиям ничего подходящего не нашлось." if source == "external" else
                "В списке «Хотим посмотреть» пока нет подходящих фильмов.")
        if response: await response.reply_text(text, reply_markup=keyboard)
        else: await _edit(update.callback_query, text, keyboard)
        return SECTION
    if response: await response.reply_text(_card(session), reply_markup=_result_keyboard(session))
    else: await _edit(update.callback_query, _card(session), _result_keyboard(session))
    return SECTION


def _card(session: dict[str, Any]) -> str:
    score: CandidateScore = session["scores"][session["index"]]; c = score.candidate
    icon, label = ("📺", "Сериал") if c.media_type == "tv" else ("🎬", "Фильм")
    head = "📚 Из вашего списка\n\n" if session["source"] == "want" else ""
    lines = [f"{head}{icon} {c.title}" + (f" ({c.year})" if c.year else ""), label]
    if c.genres: lines.append("Жанры: " + " · ".join(GENRE_LABELS.get(g, g) for g in c.genres))
    if c.external_rating is not None: lines.append(f"⭐ TMDb {c.external_rating:g}")
    if c.runtime_minutes: lines.append(f"⏱ {c.runtime_minutes // 60} ч {c.runtime_minutes % 60:02d} мин")
    if c.overview: lines.extend(["", c.overview])
    if score.explanation_reasons:
        lines.extend(["", "💡 Почему сейчас хороший вариант:" if session["source"] == "want" else "💡 Почему вам:"])
        lines.extend("• " + reason for reason in score.explanation_reasons[:3])
    return "\n".join(lines)


def _result_keyboard(session: dict[str, Any]) -> InlineKeyboardMarkup:
    token = session["id"]; score = session["scores"][session["index"]]
    rows = []
    if session["source"] == "external": rows.append([InlineKeyboardButton("➕ Хотим посмотреть", callback_data=f"filmrec:add:{token}")])
    else:
        film = next((f for f in storage.load().get("films", []) if str(f.get("external_id") or f.get("id")) == score.candidate.external_id), None)
        if film: rows.append([InlineKeyboardButton("👁 Открыть фильм", callback_data=f"filmrec:open:{token}:{film['id']}")])
    rows.append([InlineKeyboardButton("➡️ Следующий", callback_data=f"filmrec:next:{token}")])
    rows.append([InlineKeyboardButton("🔄 Ещё варианты" if session["source"] == "external" else "🔄 Пересобрать", callback_data=f"filmrec:more:{token}")])
    rows.append([InlineKeyboardButton("❌ Закрыть", callback_data="filmrec:close")])
    return InlineKeyboardMarkup(rows)


def relax_constraints(c: RecommendationConstraints) -> RecommendationConstraints:
    values = asdict(c)
    for name, empty in (("min_rating", None), ("max_runtime", None), ("min_year", None), ("max_year", None),
                        ("include_genres", frozenset()), ("exclude_genres", frozenset())):
        if values[name] not in (None, frozenset()): values[name] = empty; break
    return RecommendationConstraints(**values)


async def film_recommendation_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update): return ConversationHandler.END
    q = update.callback_query; await q.answer(); parts = (q.data or "").split(":"); action = parts[1] if len(parts)>1 else ""
    if action == "start": return await show_recommendation_menu(update, context)
    if action == "close": clear_recommendation_session(context); await _edit(q, "Подборка закрыта.", InlineKeyboardMarkup([[InlineKeyboardButton("🎬 К фильмам", callback_data="menu|films")]])); return SECTION
    if action == "actor" and len(parts)==3:
        actor = "both" if parts[2] == "both" else get_wishlist_owner_by_user(update)
        context.user_data[SESSION_KEY] = {"id": _token(), "actor": actor, "source": "external", "expires": time.time()+SESSION_TTL}
        await _edit(q, "Что ищем?", _type_keyboard()); return SECTION
    if action == "want":
        await _edit(q, "Для кого выбираем из списка?", InlineKeyboardMarkup([[InlineKeyboardButton("👫 Нам вдвоём", callback_data="filmrec:wactor:both")], [InlineKeyboardButton("👤 Мне", callback_data="filmrec:wactor:self")]])); return SECTION
    if action == "wactor" and len(parts)==3:
        return await _run(update, context, "both" if parts[2]=="both" else get_wishlist_owner_by_user(update), "want", RecommendationConstraints(limit=RESULT_LIMIT))
    if action == "type" and len(parts)==3:
        s=_session(context)
        if not s: return await _stale(q)
        await _edit(q, "✨ Подбираю варианты…")
        return await _run(update, context, s["actor"], "external", RecommendationConstraints(media_type=parts[2], limit=RESULT_LIMIT))
    if action == "retry":
        s=_session(context)
        return await _stale(q) if not s else await _run(update, context, s["actor"], s["source"], s["constraints"])
    token = parts[2] if len(parts)>2 else ""; s=_session(context, token)
    if not s: return await _stale(q)
    if action == "next":
        s["index"]=(s["index"]+1)%len(s["scores"]); await _edit(q, _card(s), _result_keyboard(s)); return SECTION
    if action == "more": return await _run(update, context, s["actor"], s["source"], s["constraints"],
                                            shown=s["shown"], generation=s.get("generation", 0) + 1)
    if action == "relax": return await _run(update, context, s["actor"], s["source"], relax_constraints(s["constraints"]))
    if action == "add":
        score=s["scores"][s["index"]]; result=create_want_film(score.candidate, added_by=get_user_name(update))
        text="✅ Добавлено в «Хотим посмотреть»" if result.created else "Уже есть в списке."
        await _edit(q, text, InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Следующий", callback_data=f"filmrec:next:{token}")], [InlineKeyboardButton("🎬 Открыть фильм", callback_data=f"filmrec:open:{token}:{result.film['id']}")], [InlineKeyboardButton("❌ Закрыть", callback_data="filmrec:close")]])); return SECTION
    if action == "open" and len(parts)==4:
        film=next((f for f in storage.load().get("films",[]) if f.get("id")==parts[3]),None)
        if not film: return await _stale(q)
        await _edit(q, _build_item("films",film), _item_keyboard("films",film,0,status_filter=film.get("status","want"))); return SECTION
    return await _stale(q)
