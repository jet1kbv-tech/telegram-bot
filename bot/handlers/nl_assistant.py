from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import AI_MAX_CLARIFICATIONS, AI_PROPOSAL_TTL_SECONDS, BOT_TIMEZONE
from bot.handlers.afisha import build_afisha_item_text
from bot.handlers.calendar import build_calendar_event_text
from bot.handlers.films import begin_film_search
from bot.services.actions.afisha import create_afisha_event
from bot.services.actions.calendar import create_personal_calendar_event
from bot.services.actions.purchases import create_purchase
from bot.services.actions.existing import mutate_existing
from bot.services.nl_dates import DateExpressionError, resolve_date_expression, resolve_time_expression, zoned_now
from bot.services.nl_dates import resolve_date_range
from bot.services.nl_intent import (
    IntentContext, IntentKind, IntentParser, IntentParserError, IntentParserTimeout, IntentParserUnavailable,
)
from bot.services.nl_proposals import ActionProposal, active_proposal, create_proposal, discard_proposal, get_proposal
from bot.services.nl_entity_resolution import EntityCandidate, resolve_entities
from bot.services.nl_query_contexts import create_query_context, get_query_context
from bot.services.queries import choose_random, next_event, query_afisha, query_calendar, query_films, query_purchases
from bot.states import (
    ADDING_CALENDAR_EVENT_TITLE, ADDING_EVENT_TITLE, ADDING_FILM_TITLE, ADDING_PURCHASE_TITLE, AI_CLARIFYING, MENU, SECTION,
)
from bot.storage import make_id, normalize_calendar_event, normalize_event, parse_calendar_event_start_dt, parse_event_dt, storage
from bot.ui.common import build_item_text
from bot.utils import ensure_access, get_allowed_profile, get_user_name, get_username, normalize_entity_title

logger = logging.getLogger(__name__)

_parser: IntentParser | None = None
_notify_calendar: Callable[[ContextTypes.DEFAULT_TYPE, Update, dict[str, Any]], Awaitable[None]] | None = None


def configure_nl_assistant(*, parser: IntentParser,
                           notify_calendar: Callable[[ContextTypes.DEFAULT_TYPE, Update, dict[str, Any]], Awaitable[None]]) -> None:
    global _parser, _notify_calendar
    _parser = parser
    _notify_calendar = notify_calendar


def _idle_state(context: ContextTypes.DEFAULT_TYPE) -> int:
    return SECTION if context.user_data.get("active_section") else MENU


def _keyboard(proposal_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Добавить", callback_data=f"ai:c:{proposal_id}")],
        [InlineKeyboardButton("✏️ Изменить", callback_data=f"ai:e:{proposal_id}")],
        [InlineKeyboardButton("❌ Отменить", callback_data=f"ai:x:{proposal_id}")],
    ])


def _mutation_keyboard(proposal: ActionProposal) -> InlineKeyboardMarkup:
    delete = proposal.intent.name.startswith("DELETE_")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Удалить" if delete else "✅ Изменить", callback_data=f"ai:c:{proposal.proposal_id}")],
        [InlineKeyboardButton("❌ Отменить", callback_data=f"ai:x:{proposal.proposal_id}")],
    ])


_MUTATION_KINDS = {kind for kind in IntentKind if kind.name.startswith(("UPDATE_", "DELETE_"))}
_QUERY_KINDS = {IntentKind.QUERY_PURCHASES, IntentKind.QUERY_FILMS, IntentKind.QUERY_CALENDAR, IntentKind.QUERY_AFISHA}
_QUERY_PAGE_SIZE = 10


class _WaitingResponse:
    """Resolve one progress message, falling back to a normal reply on Telegram errors."""

    def __init__(self, source: Any, waiting: Any | None) -> None:
        self._source, self._waiting, self.resolved = source, waiting, False

    async def reply_text(self, text: str, **kwargs: Any) -> Any:
        if self._waiting is not None and not self.resolved:
            try:
                result = await self._waiting.edit_text(text, **kwargs)
                self.resolved = True
                return result
            except Exception:  # Telegram/network failures must not strand the progress UI.
                logger.warning("NL waiting message edit failed; using reply fallback")
        result = await self._source.reply_text(text, **kwargs)
        self.resolved = True
        return result

    async def discard(self) -> None:
        if self._waiting is not None and not self.resolved:
            try:
                await self._waiting.delete()
            except Exception:
                logger.warning("NL waiting message delete failed")
        self.resolved = True


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]])


def _clarification_keyboard(proposal_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отменить", callback_data=f"ai:x:{proposal_id}")]])


async def _safe_query_edit(query: Any, text: str, **kwargs: Any) -> Any:
    """Edit a callback message, or reply without leaving a broken callback flow."""
    try:
        return await query.edit_message_text(text, **kwargs)
    except Exception:
        logger.warning("NL callback message edit failed; using reply fallback")
        message = getattr(query, "message", None)
        if message is not None:
            return await message.reply_text(text, **kwargs)
        return None


def _human_date(value: Any) -> str:
    raw = str(value or "")
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return raw or "—"


_VALUE_LABELS = {
    "high": "высокий", "medium": "средний", "low": "низкий", "none": "не указан",
    "planned": "запланирована", "bought": "куплена", "want": "хочу посмотреть", "watched": "просмотрено",
    "current_user": "я", "": "не назначен",
}


def _human_value(field: str, value: Any) -> str:
    if field == "date":
        return _human_date(value)
    if field == "price" and value is not None:
        return f"{value:,} ₽".replace(",", " ")
    return _VALUE_LABELS.get(str(value), str(value or "—"))


def _candidate_label(candidate: EntityCandidate) -> str:
    item = candidate.item
    when = item.get("date")
    clock = item.get("start_time") or item.get("time")
    values = (_human_date(when), str(clock)) if when else (str(clock),)
    suffix = " — " + ", ".join(value for value in values if value) if when or clock else ""
    return f"{item.get('title') or 'Без названия'}{suffix}"


def _select_candidate(proposal: ActionProposal, candidate: EntityCandidate, actor_name: str) -> None:
    args, item = proposal.arguments, candidate.item
    changes: dict[str, Any] = {}
    mapping = {"date": "date", "time": "time", "start_time": "start_time"}
    for field, value in list(args.items()):
        if field == "target" or field.startswith("_") or value is None:
            continue
        target_field = mapping.get(field, field)
        comparable = actor_name if field == "buyer" and value == "current_user" else "" if value in {"none"} else value
        old = candidate.bucket if field == "status" and proposal.intent in {IntentKind.UPDATE_PURCHASE} else item.get(target_field)
        if old != comparable:
            changes[target_field if field != "status" else field] = value
    expected = dict(item) if proposal.intent.name.startswith("DELETE_") else {
        (field if field != "status" else field): (candidate.bucket if field == "status" and proposal.intent is IntentKind.UPDATE_PURCHASE else item.get(field))
        for field in changes
    }
    proposal.arguments.update({"_id": candidate.item_id, "_bucket": candidate.bucket, "_selected": item,
                               "_changes": changes, "_expected": expected, "_actor_name": actor_name})


def _prepare(kind: IntentKind, arguments: dict[str, Any], now: datetime) -> tuple[dict[str, Any], list[str]]:
    args = dict(arguments)
    missing: list[str] = []
    if kind in {
        IntentKind.ADD_PURCHASE, IntentKind.UPDATE_PURCHASE,
        IntentKind.ADD_PERSONAL_CALENDAR_EVENT, IntentKind.UPDATE_CALENDAR_EVENT,
        IntentKind.ADD_AFISHA_EVENT, IntentKind.UPDATE_AFISHA_EVENT,
    } and isinstance(args.get("title"), str):
        # Normalize only the proposed storage-visible name. In particular, do
        # not alter ``target`` before entity resolution or TMDB search queries.
        args["title"] = normalize_entity_title(args["title"])
    if kind is IntentKind.ADD_PURCHASE:
        args["buyer"] = "current_user" if args.get("buyer") == "current_user" else ""
    elif kind in {IntentKind.ADD_PERSONAL_CALENDAR_EVENT, IntentKind.ADD_AFISHA_EVENT}:
        if kind is IntentKind.ADD_PERSONAL_CALENDAR_EVENT:
            args.pop("owner", None)
        date_expr = args.pop("date_expression", None)
        time_expr = args.pop("time_expression", None)
        if date_expr:
            try:
                args["date"] = resolve_date_expression(date_expr, now=now, timezone=BOT_TIMEZONE)
            except DateExpressionError:
                missing.append("date")
        else:
            missing.append("date")
        if time_expr:
            try:
                target = "start_time" if kind is IntentKind.ADD_PERSONAL_CALENDAR_EVENT else "time"
                args[target] = resolve_time_expression(time_expr)
            except DateExpressionError:
                missing.append("time")
        else:
            missing.append("time")
        end_time_expr = args.pop("end_time_expression", None)
        if end_time_expr:
            try:
                args["end_time"] = resolve_time_expression(end_time_expr)
            except DateExpressionError:
                missing.append("end_time")
        else:
            args["end_time"] = ""
        if kind is IntentKind.ADD_AFISHA_EVENT:
            end_date_expr = args.pop("end_date_expression", None)
            if end_date_expr:
                try:
                    args["end_date"] = resolve_date_expression(end_date_expr, now=now, timezone=BOT_TIMEZONE)
                except DateExpressionError:
                    missing.append("end_date")
            else:
                args["end_date"] = ""
    elif kind in {IntentKind.QUERY_CALENDAR, IntentKind.QUERY_AFISHA}:
        from_expression, to_expression = args.get("date_from"), args.get("date_to")
        if from_expression or to_expression:
            start, _ = resolve_date_range(str(from_expression or to_expression), now=now, timezone=BOT_TIMEZONE)
            _, end = resolve_date_range(str(to_expression or from_expression), now=now, timezone=BOT_TIMEZONE)
            args["date_from"], args["date_to"] = start, end
    elif kind in {IntentKind.UPDATE_CALENDAR_EVENT, IntentKind.UPDATE_AFISHA_EVENT}:
        for source, target in (("date_expression", "date"), ("time_expression", "start_time" if kind is IntentKind.UPDATE_CALENDAR_EVENT else "time")):
            expression = args.pop(source, None)
            if expression:
                try:
                    args[target] = resolve_date_expression(expression, now=now, timezone=BOT_TIMEZONE) if target == "date" else resolve_time_expression(expression)
                except DateExpressionError:
                    missing.append("date" if target == "date" else "time")
    return args, missing


def _validate_prepared(kind: IntentKind, args: dict[str, Any]) -> None:
    """Apply canonical domain validation without touching storage."""
    if kind is IntentKind.ADD_PURCHASE:
        if not str(args.get("title") or "").strip() or args.get("priority") not in {None, "high", "medium", "low"}:
            raise ValueError("invalid_purchase")
        return
    if kind is IntentKind.ADD_PERSONAL_CALENDAR_EVENT:
        candidate = {"id": make_id(), "owner": "vova", **args, "source": "manual", "source_id": ""}
        if normalize_calendar_event(candidate, "vova") is None:
            raise ValueError("invalid_calendar_event")
        return
    if kind is IntentKind.ADD_AFISHA_EVENT:
        candidate = {"id": make_id(), **args, "status": "active"}
        if normalize_event(candidate) is None:
            raise ValueError("invalid_afisha_event")


async def nl_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if _parser is None or not await ensure_access(update):
        return ConversationHandler.END if _parser is None else _idle_state(context)
    message = update.effective_message
    if message is None:
        return _idle_state(context)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    waiting = None
    try:
        waiting = await message.reply_text("⏳ Разбираю команду…")
    except Exception:
        logger.warning("NL waiting message send failed; continuing without progress message")
    response = _WaitingResponse(message, waiting)
    now = zoned_now(BOT_TIMEZONE)
    try:
        parsed = await _parser.parse(message.text or "", IntentContext(
            actor_key=get_username(update), local_now=now, timezone=BOT_TIMEZONE,
            active_section=context.user_data.get("active_section"),
        ))
        if parsed.intent is IntentKind.NO_ACTION:
            await response.reply_text(
                "Я пока лучше всего умею работать с нашими планами 🙂\n"
                "Попроси меня добавить, изменить, удалить или найти что-нибудь "
                "в покупках, фильмах, календаре или Афише.",
                reply_markup=_menu_keyboard(),
            )
            return _idle_state(context)
        if parsed.intent is IntentKind.UNSUPPORTED:
            await response.reply_text("С этой командой я пока не умею работать. Могу помочь с покупками, фильмами, календарём и Афишей.", reply_markup=_menu_keyboard())
            return _idle_state(context)
        arguments, missing = _prepare(parsed.intent, parsed.arguments, now)
        if parsed.intent in _QUERY_KINDS:
            await _answer_query(response, context, parsed.intent, arguments, update, now)
            return _idle_state(context)
        if parsed.intent in _MUTATION_KINDS:
            update_fields = [key for key, value in arguments.items() if key != "target" and value is not None]
            if parsed.intent.name.startswith("UPDATE_") and not update_fields and not missing:
                await response.reply_text("Что именно нужно изменить? Сформулируй команду ещё раз.", reply_markup=_menu_keyboard())
                return _idle_state(context)
            profile = get_allowed_profile(update) or {}
            owner = str(profile.get("wishlist_owner") or "")
            candidates = resolve_entities(storage.load(), parsed.intent, arguments["target"], owner=owner,
                                           include_past=False, now=now, timezone=BOT_TIMEZONE)
            logger.info("NL entity resolution intent=%s status=%s candidate_count=%s", parsed.intent.value, "found" if candidates else "missing", len(candidates))
            if not candidates:
                await response.reply_text("Не нашёл такую запись. Проверь название или открой нужный раздел вручную.", reply_markup=_menu_keyboard())
                return _idle_state(context)
            proposal = create_proposal(context.user_data, intent=parsed.intent, arguments=arguments,
                                       actor_key=get_username(update), now=now, ttl_seconds=AI_PROPOSAL_TTL_SECONDS,
                                       missing_fields=missing)
            proposal.arguments["_candidates"] = [{"id": c.item_id, "bucket": c.bucket, "item": c.item} for c in candidates]
            if len(candidates) > 1:
                lines = ["Нашёл несколько похожих записей. Какую выбрать?", ""] + [f"{index + 1}. {_candidate_label(c)}" for index, c in enumerate(candidates)]
                buttons = [[InlineKeyboardButton(f"{index + 1}. {_candidate_label(candidate)}"[:60], callback_data=f"ai:r:{proposal.proposal_id}:{index}")] for index, candidate in enumerate(candidates)]
                buttons.append([InlineKeyboardButton("❌ Отменить", callback_data=f"ai:x:{proposal.proposal_id}")])
                await response.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
                return _idle_state(context)
            _select_candidate(proposal, candidates[0], get_user_name(update))
            if not proposal.arguments["_changes"] and parsed.intent.name.startswith("UPDATE_") and not missing:
                discard_proposal(context.user_data, proposal)
                await response.reply_text("Это значение уже установлено.", reply_markup=_menu_keyboard())
                return _idle_state(context)
            if missing:
                await response.reply_text(_clarification_prompt(missing[0], proposal.intent), reply_markup=_clarification_keyboard(proposal.proposal_id))
                return AI_CLARIFYING
            await response.reply_text(_preview(proposal), reply_markup=_mutation_keyboard(proposal))
            return _idle_state(context)
        if not missing:
            _validate_prepared(parsed.intent, arguments)
    except IntentParserTimeout:
        await response.reply_text("Ответ занял слишком много времени. Попробуй отправить команду ещё раз.", reply_markup=_menu_keyboard())
        return _idle_state(context)
    except IntentParserUnavailable:
        await response.reply_text("Сейчас не получается разобрать команду. Попробуй чуть позже или открой меню.", reply_markup=_menu_keyboard())
        return _idle_state(context)
    except (IntentParserError, ValueError, DateExpressionError):
        logger.info("NL intent parsing or validation failed", exc_info=True)
        await response.reply_text("Не получилось надёжно разобрать команду. Попробуй сформулировать её чуть иначе.", reply_markup=_menu_keyboard())
        return _idle_state(context)

    proposal = create_proposal(context.user_data, intent=parsed.intent, arguments=arguments,
                               actor_key=get_username(update), now=now, ttl_seconds=AI_PROPOSAL_TTL_SECONDS,
                               missing_fields=missing)
    logger.info("NL proposal created intent=%s missing=%s", proposal.intent.value, len(missing))
    if missing:
        await response.reply_text(_clarification_prompt(missing[0], proposal.intent), reply_markup=_clarification_keyboard(proposal.proposal_id))
        return AI_CLARIFYING
    keyboard = _mutation_keyboard(proposal) if proposal.intent in _MUTATION_KINDS else _keyboard(proposal.proposal_id)
    await response.reply_text(_preview(proposal), reply_markup=keyboard)
    return _idle_state(context)


def _query_keyboard(token: str, *, page: int = 0, more: bool = False, random_mode: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if more:
        rows.append([InlineKeyboardButton("Показать ещё", callback_data=f"aiq:{token}:p:{page + 1}")])
    if random_mode:
        rows.append([InlineKeyboardButton("🎲 Другой вариант", callback_data=f"aiq:{token}:r")])
    rows.append([InlineKeyboardButton("🏠 В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def _event_line(item: dict[str, Any], calendar: bool) -> str:
    stamp = parse_calendar_event_start_dt(item) if calendar else parse_event_dt(item)
    return f"• {stamp.strftime('%d.%m %H:%M') if stamp else 'Дата не указана'} — {item.get('title') or 'Без названия'}"


async def _answer_query(message: Any, context: ContextTypes.DEFAULT_TYPE, kind: IntentKind, args: dict[str, Any],
                        update: Update, now: datetime, *, page: int = 0, token: str | None = None, edit: bool = False) -> None:
    data = storage.load()
    actor_name = get_user_name(update)
    if kind is IntentKind.QUERY_PURCHASES:
        result = query_purchases(data, **{key: args[key] for key in ("status", "priority", "buyer")}, actor_name=actor_name)
    elif kind is IntentKind.QUERY_FILMS:
        result = query_films(data, **{key: args[key] for key in ("status", "media_type", "genre")})
    else:
        common = {key: args.get(key) for key in ("date_from", "date_to", "target")}
        if kind is IntentKind.QUERY_CALENDAR:
            profile = get_allowed_profile(update) or {}
            # Owner is application-derived. No model field can override it.
            result = query_calendar(data, owner=str(profile.get("wishlist_owner") or ""), now=now, **common)
        else:
            result = query_afisha(data, now=now, **common)
    operation = args["operation"]
    if token is None and operation in {"list", "random"}:
        token = create_query_context(context.user_data, intent=kind, arguments=args, actor_key=get_username(update),
                                     now=now, ttl_seconds=AI_PROPOSAL_TTL_SECONDS).token
    markup = _menu_keyboard()
    if operation == "next":
        parser = parse_calendar_event_start_dt if kind is IntentKind.QUERY_CALENDAR else parse_event_dt
        item = next_event(result, parser, now)
        text = "Ближайшее событие\n\n" + _event_line(item, kind is IntentKind.QUERY_CALENDAR) if item else "Впереди подходящих событий нет."
    elif operation == "count":
        text = f"Найдено: {result.total}"
    elif operation == "sum":
        text = f"Общая стоимость: {result.amount:,} ₽".replace(",", " ")
        if result.missing_prices:
            text += f"\nБез указанной цены: {result.missing_prices}"
    elif operation == "random":
        item = choose_random(result)
        text = "🎲 Сегодня смотрим:\n\n" + build_item_text("films", item) if item else "В списке на просмотр нет подходящих фильмов."
        markup = _query_keyboard(token, random_mode=bool(item))
    else:
        start = page * _QUERY_PAGE_SIZE
        shown = result.items[start:start + _QUERY_PAGE_SIZE]
        if not shown:
            text = {IntentKind.QUERY_PURCHASES: "Подходящих покупок пока нет.", IntentKind.QUERY_FILMS: "В списке нет подходящих фильмов.", IntentKind.QUERY_CALENDAR: "На выбранный период у тебя ничего не запланировано.", IntentKind.QUERY_AFISHA: "На выбранный период в Афише пока ничего нет."}[kind]
        elif kind is IntentKind.QUERY_PURCHASES:
            lines = [f"• {item.get('title') or 'Без названия'}" + (f" — {item['price']:,} ₽".replace(",", " ") if item.get("price") is not None else "") for item in shown]
            text = "🛍 Покупки\n\n" + "\n".join(lines)
        elif kind is IntentKind.QUERY_FILMS:
            lines = [f"• {item.get('title') or 'Без названия'}" + (f" ({item['year']})" if item.get("year") else "") for item in shown]
            text = "🎬 Фильмы и сериалы\n\n" + "\n".join(lines)
        else:
            text = ("📅 Мой календарь" if kind is IntentKind.QUERY_CALENDAR else "🗓 Афиша") + "\n\n" + "\n".join(_event_line(item, kind is IntentKind.QUERY_CALENDAR) for item in shown)
        if shown:
            text += f"\n\nПоказано {min(start + len(shown), result.total)} из {result.total}"
        markup = _query_keyboard(token, page=page, more=start + len(shown) < result.total)
    logger.info("NL query intent=%s operation=%s result_count=%s", kind.value, operation, result.total)
    if edit:
        await _safe_query_edit(message, text, reply_markup=markup)
    else:
        await message.reply_text(text, reply_markup=markup)


async def nl_query_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    token = parts[1] if len(parts) > 1 else ""
    now = zoned_now(BOT_TIMEZONE)
    saved = get_query_context(context.user_data, token, actor_key=get_username(update), now=now)
    if saved is None:
        await _safe_query_edit(query, "Этот запрос уже устарел. Отправь его ещё раз.", reply_markup=_menu_keyboard())
        return _idle_state(context)
    page = 0
    if len(parts) == 4 and parts[2] == "p":
        try:
            page = max(0, int(parts[3]))
        except ValueError:
            page = 0
    await _answer_query(query, context, saved.intent, saved.arguments, update, now, page=page, token=token, edit=True)
    return _idle_state(context)


def _clarification_prompt(field: str, intent: IntentKind | None = None) -> str:
    return {
        "date": "На какую дату поставить событие?", "time": "Во сколько начнётся событие?", "end_time": "Во сколько событие закончится?",
        "end_date": "В какой день событие закончится?", "title": "Как назвать?",
        "price": "Сколько стоит покупка?", "target": "Какую запись нужно изменить?", "place": "Где пройдёт событие?",
    }.get(field, "Какую деталь нужно добавить?")


async def nl_clarification_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    now = zoned_now(BOT_TIMEZONE)
    proposal = active_proposal(context.user_data, actor_key=get_username(update), now=now)
    message = update.effective_message
    if proposal is None or message is None:
        if message:
            await message.reply_text("Это предложение уже устарело. Напиши команду ещё раз.", reply_markup=_menu_keyboard())
        return _idle_state(context)
    if proposal.clarification_count >= AI_MAX_CLARIFICATIONS:
        discard_proposal(context.user_data, proposal)
        await message.reply_text("Не получилось собрать все данные. Добавь событие через обычную форму.", reply_markup=_menu_keyboard())
        return _idle_state(context)
    field = proposal.missing_fields[0]
    try:
        if field in {"date", "end_date"}:
            proposal.arguments[field] = resolve_date_expression(message.text or "", now=now, timezone=BOT_TIMEZONE)
        elif field in {"time", "end_time"}:
            target = "start_time" if field == "time" and proposal.intent is IntentKind.ADD_PERSONAL_CALENDAR_EVENT else field
            proposal.arguments[target] = resolve_time_expression(message.text or "")
        proposal.missing_fields.pop(0)
        proposal.clarification_count += 1
    except DateExpressionError:
        proposal.clarification_count += 1
        await message.reply_text(f"Не смог распознать. {_clarification_prompt(field, proposal.intent)}", reply_markup=_clarification_keyboard(proposal.proposal_id))
        return AI_CLARIFYING
    if proposal.missing_fields:
        await message.reply_text(_clarification_prompt(proposal.missing_fields[0], proposal.intent), reply_markup=_clarification_keyboard(proposal.proposal_id))
        return AI_CLARIFYING
    if proposal.intent in _MUTATION_KINDS:
        selected = EntityCandidate(str(proposal.arguments["_id"]), str(proposal.arguments["_bucket"]), proposal.arguments["_selected"])
        _select_candidate(proposal, selected, get_user_name(update))
        if not proposal.arguments["_changes"]:
            discard_proposal(context.user_data, proposal)
            await message.reply_text("Это значение уже установлено.", reply_markup=_menu_keyboard())
            return _idle_state(context)
    try:
        _validate_prepared(proposal.intent, proposal.arguments)
    except ValueError:
        discard_proposal(context.user_data, proposal)
        await message.reply_text("Эти дата или время не образуют корректное событие. Напиши команду ещё раз.", reply_markup=_menu_keyboard())
        return _idle_state(context)
    keyboard = _mutation_keyboard(proposal) if proposal.intent in _MUTATION_KINDS else _keyboard(proposal.proposal_id)
    await message.reply_text(_preview(proposal), reply_markup=keyboard)
    return _idle_state(context)


def _preview(proposal: ActionProposal) -> str:
    args = proposal.arguments
    if proposal.intent in _MUTATION_KINDS:
        item, changes = args["_selected"], args["_changes"]
        if proposal.intent.name.startswith("DELETE_"):
            parts = [_human_date(item["date"])] if item.get("date") else []
            if item.get("start_time") or item.get("time"):
                parts.append(str(item.get("start_time") or item.get("time")))
            when = " в ".join(parts)
            details = f"\n{when}" if when else ""
            return f"🗑 Удалить\n\n{item.get('title') or 'Без названия'}{details}\n\nПока ничего не удалено."
        labels = {"title": "Название", "price": "Стоимость", "priority": "Приоритет", "buyer": "Исполнитель", "status": "Статус", "comment": "Комментарий", "link": "Ссылка", "date": "Дата", "time": "Время", "start_time": "Время"}
        lines = ["✏️ Изменить", "", str(item.get("title") or "Без названия"), ""]
        lines.extend(f"{labels.get(field, field)}: {_human_value(field, item.get(field))} → {_human_value(field, value)}" for field, value in changes.items())
        lines.extend(["", "Пока ничего не изменено."])
        return "\n".join(lines)
    if proposal.intent is IntentKind.ADD_MOVIE_OR_TV:
        return f"🎬 Добавить фильм или сериал\n\n{args['query']}\n\nПока ничего не добавлено."
    if proposal.intent is IntentKind.ADD_PURCHASE:
        lines = ["➕ Добавить покупку", "", f"Название: {args['title']}"]
        if args.get("price") is not None:
            lines.append(f"Стоимость: {args['price']:,} ₽".replace(",", " "))
        labels = {"high": "Высокий", "medium": "Средний", "low": "Низкий"}
        if args.get("priority"):
            lines.append(f"Приоритет: {labels[args['priority']]}")
        if args.get("buyer") == "current_user":
            lines.append("Куплю я")
        if args.get("link"):
            lines.append(f"Ссылка: {args['link']}")
        if args.get("comment"):
            lines.append(f"Комментарий: {args['comment']}")
        lines.extend(["", "Пока ничего не добавлено."])
        return "\n".join(lines)
    if proposal.intent is IntentKind.ADD_PERSONAL_CALENDAR_EVENT:
        lines = ["➕ Добавить в календарь", "", str(args["title"]), _human_date(args["date"]), str(args["start_time"])]
        if args.get("end_time"):
            lines[-1] += f"–{args['end_time']}"
        if args.get("comment"):
            lines.append(f"Комментарий: {args['comment']}")
        lines.extend(["", "Пока ничего не добавлено."])
        return "\n".join(lines)
    lines = ["➕ Добавить в Афишу", "", str(args["title"]), _human_date(args["date"]), str(args["time"])]
    if args.get("place"):
        lines.append(f"Место: {args['place']}")
    if args.get("link"):
        lines.append(f"Ссылка: {args['link']}")
    lines.extend(["", "Пока ничего не добавлено."])
    return "\n".join(lines)


async def nl_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    action, proposal_id = (parts[1], parts[2]) if len(parts) >= 3 else ("", "")
    now = zoned_now(BOT_TIMEZONE)
    proposal = get_proposal(context.user_data, proposal_id, actor_key=get_username(update), now=now)
    if proposal is None:
        await _safe_query_edit(query, "Это предложение уже устарело. Отправь команду ещё раз.", reply_markup=_menu_keyboard())
        return _idle_state(context)
    if action == "x":
        proposal.status = "cancelled"
        discard_proposal(context.user_data, proposal)
        logger.info("NL proposal cancelled intent=%s", proposal.intent.value)
        await _safe_query_edit(query, "Действие отменено. Ничего не изменилось.", reply_markup=_menu_keyboard())
        return _idle_state(context)
    if action == "r" and len(parts) == 4:
        candidates = proposal.arguments.get("_candidates", [])
        try:
            raw = candidates[int(parts[3])]
        except (ValueError, IndexError, TypeError):
            await _safe_query_edit(query, "Вариант больше недоступен. Напиши команду ещё раз.", reply_markup=_menu_keyboard())
            return _idle_state(context)
        _select_candidate(proposal, EntityCandidate(raw["id"], raw["bucket"], raw["item"]), get_user_name(update))
        if proposal.missing_fields:
            await _safe_query_edit(query, _clarification_prompt(proposal.missing_fields[0], proposal.intent), reply_markup=_clarification_keyboard(proposal.proposal_id))
            return AI_CLARIFYING
        if not proposal.arguments["_changes"] and proposal.intent.name.startswith("UPDATE_"):
            discard_proposal(context.user_data, proposal)
            await _safe_query_edit(query, "Это значение уже установлено.", reply_markup=_menu_keyboard())
            return _idle_state(context)
        await _safe_query_edit(query, _preview(proposal), reply_markup=_mutation_keyboard(proposal))
        return _idle_state(context)
    if action == "e":
        return await _edit_proposal(update, context, proposal)
    if action != "c" or proposal.status != "pending" or proposal.missing_fields:
        await _safe_query_edit(query, "Это предложение уже устарело. Отправь команду ещё раз.", reply_markup=_menu_keyboard())
        return _idle_state(context)
    proposal.status = "executing"
    try:
        if proposal.intent in _MUTATION_KINDS:
            result = mutate_existing(proposal.intent, proposal.arguments)
            if result.status == "conflict":
                discard_proposal(context.user_data, proposal)
                await _safe_query_edit(query, "Объект изменился с момента подтверждения. Проверь актуальные данные и попробуй ещё раз.", reply_markup=_menu_keyboard())
                return _idle_state(context)
            if result.status in {"missing", "already_deleted"}:
                discard_proposal(context.user_data, proposal)
                await _safe_query_edit(query, "Объекта больше нет.", reply_markup=_menu_keyboard())
                return SECTION
            if result.status not in {"updated", "deleted"}:
                raise ValueError("mutation_failed")
            text = "Объект удалён." if result.status == "deleted" else "Объект обновлён."
        if proposal.intent is IntentKind.ADD_MOVIE_OR_TV:
            discard_proposal(context.user_data, proposal)
            await _safe_query_edit(query, "Ищу фильм или сериал…")
            return await begin_film_search(update, context, str(proposal.arguments["query"]))
        elif proposal.intent is IntentKind.ADD_PURCHASE:
            args = dict(proposal.arguments)
            args["buyer"] = get_user_name(update) if args.get("buyer") == "current_user" else ""
            item = create_purchase(args)
            text = "Покупка добавлена:\n\n" + _preview(ActionProposal("", proposal.intent, args, "", now, now))
        elif proposal.intent is IntentKind.ADD_PERSONAL_CALENDAR_EVENT:
            profile = get_allowed_profile(update) or {}
            owner = str(profile.get("wishlist_owner") or "")
            item = create_personal_calendar_event(proposal.arguments, owner=owner)
            if _notify_calendar is not None:
                await _notify_calendar(context, update, item)
            text = "Событие сохранено:\n\n" + build_calendar_event_text(item)
        elif proposal.intent is IntentKind.ADD_AFISHA_EVENT:
            item = create_afisha_event(proposal.arguments)
            text = "Событие сохранено:\n\n" + build_afisha_item_text(item)
        elif proposal.intent not in _MUTATION_KINDS:
            raise ValueError("unsupported_proposal")
    except ValueError:
        proposal.status = "pending"
        logger.info("NL proposal domain validation failed intent=%s", proposal.intent.value, exc_info=True)
        await _safe_query_edit(query, "Не удалось применить предложение. Проверь данные через «Изменить».", reply_markup=_keyboard(proposal.proposal_id))
        return _idle_state(context)
    proposal.status = "confirmed"
    discard_proposal(context.user_data, proposal)
    logger.info("NL proposal confirmed intent=%s", proposal.intent.value)
    await _safe_query_edit(query, text, reply_markup=_menu_keyboard())
    return SECTION


async def _edit_proposal(update: Update, context: ContextTypes.DEFAULT_TYPE, proposal: ActionProposal) -> int:
    query = update.callback_query
    args = proposal.arguments
    discard_proposal(context.user_data, proposal)
    if proposal.intent is IntentKind.ADD_MOVIE_OR_TV:
        await _safe_query_edit(query, "Отправь новое название фильма или сериала:")
        return ADDING_FILM_TITLE
    if proposal.intent is IntentKind.ADD_PURCHASE:
        context.user_data.update({"purchase_title": args.get("title"), "purchase_link": args.get("link") or "",
                                  "purchase_price": args.get("price"), "purchase_priority": args.get("priority") or ""})
        await _safe_query_edit(query, "Изменение через обычную форму. Отправь название покупки:")
        return ADDING_PURCHASE_TITLE
    if proposal.intent is IntentKind.ADD_PERSONAL_CALENDAR_EVENT:
        profile = get_allowed_profile(update) or {}
        context.user_data["calendar_owner"] = profile.get("wishlist_owner")
        await _safe_query_edit(query, "Изменение через обычную форму. Отправь название события:")
        return ADDING_CALENDAR_EVENT_TITLE
    await _safe_query_edit(query, "Изменение через обычную форму. Отправь название события:")
    return ADDING_EVENT_TITLE
