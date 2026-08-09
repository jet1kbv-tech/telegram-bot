from __future__ import annotations

import logging
import re
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
from bot.services.nl_dates import DateExpressionError, resolve_date_expression, resolve_time_expression, zoned_now
from bot.services.nl_intent import (
    IntentContext, IntentKind, IntentParser, IntentParserError, IntentParserTimeout,
)
from bot.services.nl_proposals import ActionProposal, active_proposal, create_proposal, discard_proposal, get_proposal
from bot.states import (
    ADDING_CALENDAR_EVENT_TITLE, ADDING_EVENT_TITLE, ADDING_FILM_TITLE, ADDING_PURCHASE_TITLE, AI_CLARIFYING, MENU, SECTION,
)
from bot.storage import make_id, normalize_calendar_event, normalize_event
from bot.utils import ensure_access, get_allowed_profile, get_user_name, get_username

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
        [InlineKeyboardButton("❌ Отмена", callback_data=f"ai:x:{proposal_id}")],
    ])


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]])


def _parse_price(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    text = value.strip().casefold().replace("ё", "е")
    multiplier = 1
    if re.search(r"\b(тыс\.?|тысяч[аи]?|тысячи?)\b", text):
        multiplier = 1000
        text = re.sub(r"\b(тыс\.?|тысяч[аи]?|тысячи?)\b", "", text)
    text = re.sub(r"(руб(?:лей|ля|ль)?\.?|₽|р\.)", "", text)
    text = text.replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace(",", ".")
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError("invalid_price") from exc
    result = number * multiplier
    if result < 0 or result > 1_000_000_000 or not result.is_integer():
        raise ValueError("invalid_price")
    return int(result)


def _prepare(kind: IntentKind, arguments: dict[str, Any], now: datetime) -> tuple[dict[str, Any], list[str]]:
    args = dict(arguments)
    missing: list[str] = []
    if kind is IntentKind.ADD_PURCHASE:
        args["price"] = _parse_price(args.pop("price_text", None))
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
    now = zoned_now(BOT_TIMEZONE)
    try:
        parsed = await _parser.parse(message.text or "", IntentContext(
            actor_key=get_username(update), local_now=now, timezone=BOT_TIMEZONE,
            active_section=context.user_data.get("active_section"),
        ))
        if parsed.intent is IntentKind.NO_ACTION:
            return _idle_state(context)
        if parsed.intent is IntentKind.UNSUPPORTED:
            await message.reply_text("Я пока умею добавлять фильмы, покупки и события в твой календарь или Афишу.", reply_markup=_menu_keyboard())
            return _idle_state(context)
        arguments, missing = _prepare(parsed.intent, parsed.arguments, now)
        if not missing:
            _validate_prepared(parsed.intent, arguments)
    except IntentParserTimeout:
        await message.reply_text("Не удалось разобрать команду вовремя. Попробуй ещё раз или открой меню.", reply_markup=_menu_keyboard())
        return _idle_state(context)
    except (IntentParserError, ValueError, DateExpressionError):
        logger.info("NL intent parsing or validation failed", exc_info=True)
        await message.reply_text("Не понял, что именно нужно сделать. Попробуй ещё раз.", reply_markup=_menu_keyboard())
        return _idle_state(context)

    proposal = create_proposal(context.user_data, intent=parsed.intent, arguments=arguments,
                               actor_key=get_username(update), now=now, ttl_seconds=AI_PROPOSAL_TTL_SECONDS,
                               missing_fields=missing)
    logger.info("NL proposal created intent=%s missing=%s", proposal.intent.value, len(missing))
    if missing:
        await message.reply_text(_clarification_prompt(missing[0]))
        return AI_CLARIFYING
    await message.reply_text(_preview(proposal), reply_markup=_keyboard(proposal.proposal_id))
    return _idle_state(context)


def _clarification_prompt(field: str) -> str:
    return {
        "date": "На какую дату?", "time": "Во сколько?", "end_time": "Во сколько событие закончится?",
        "end_date": "На какую дату приходится окончание?",
    }.get(field, "Уточни недостающие данные:")


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
        await message.reply_text(f"Не смог распознать. {_clarification_prompt(field)}")
        return AI_CLARIFYING
    if proposal.missing_fields:
        await message.reply_text(_clarification_prompt(proposal.missing_fields[0]))
        return AI_CLARIFYING
    try:
        _validate_prepared(proposal.intent, proposal.arguments)
    except ValueError:
        discard_proposal(context.user_data, proposal)
        await message.reply_text("Эти дата или время не образуют корректное событие. Напиши команду ещё раз.", reply_markup=_menu_keyboard())
        return _idle_state(context)
    await message.reply_text(_preview(proposal), reply_markup=_keyboard(proposal.proposal_id))
    return _idle_state(context)


def _preview(proposal: ActionProposal) -> str:
    args = proposal.arguments
    if proposal.intent is IntentKind.ADD_MOVIE_OR_TV:
        return f"🎬 Найти и добавить в фильмы?\n\n{args['query']}"
    if proposal.intent is IntentKind.ADD_PURCHASE:
        lines = ["🛒 Добавить в покупки?", "", str(args["title"])]
        if args.get("price") is not None:
            lines.append(f"💰 {args['price']:,} ₽".replace(",", " "))
        labels = {"high": "🔴 Высокий приоритет", "medium": "🟡 Средний приоритет", "low": "🟢 Низкий приоритет"}
        if args.get("priority"):
            lines.append(labels[args["priority"]])
        if args.get("buyer") == "current_user":
            lines.append("🙋 Куплю я")
        if args.get("link"):
            lines.append(f"🔗 {args['link']}")
        if args.get("comment"):
            lines.append(f"💬 {args['comment']}")
        return "\n".join(lines)
    if proposal.intent is IntentKind.ADD_PERSONAL_CALENDAR_EVENT:
        lines = ["📅 Добавить в мой календарь?", "", str(args["title"]), str(args["date"]), str(args["start_time"])]
        if args.get("end_time"):
            lines[-1] += f"–{args['end_time']}"
        if args.get("comment"):
            lines.append(f"💬 {args['comment']}")
        return "\n".join(lines)
    lines = ["🗓 Добавить в Афишу?", "", str(args["title"]), str(args["date"]), str(args["time"])]
    if args.get("place"):
        lines.append(f"📍 {args['place']}")
    if args.get("link"):
        lines.append(f"🔗 {args['link']}")
    return "\n".join(lines)


async def nl_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    action, proposal_id = (parts[1], parts[2]) if len(parts) == 3 else ("", "")
    now = zoned_now(BOT_TIMEZONE)
    proposal = get_proposal(context.user_data, proposal_id, actor_key=get_username(update), now=now)
    if proposal is None:
        await query.edit_message_text("Это предложение уже устарело. Напиши команду ещё раз.", reply_markup=_menu_keyboard())
        return _idle_state(context)
    if action == "x":
        proposal.status = "cancelled"
        discard_proposal(context.user_data, proposal)
        logger.info("NL proposal cancelled intent=%s", proposal.intent.value)
        await query.edit_message_text("Отменено.", reply_markup=_menu_keyboard())
        return _idle_state(context)
    if action == "e":
        return await _edit_proposal(update, context, proposal)
    if action != "c" or proposal.status != "pending" or proposal.missing_fields:
        await query.edit_message_text("Это предложение уже устарело. Напиши команду ещё раз.", reply_markup=_menu_keyboard())
        return _idle_state(context)
    proposal.status = "executing"
    try:
        if proposal.intent is IntentKind.ADD_MOVIE_OR_TV:
            discard_proposal(context.user_data, proposal)
            await query.edit_message_text("Ищу фильм или сериал…")
            return await begin_film_search(update, context, str(proposal.arguments["query"]))
        if proposal.intent is IntentKind.ADD_PURCHASE:
            args = dict(proposal.arguments)
            args["buyer"] = get_user_name(update) if args.get("buyer") == "current_user" else ""
            item = create_purchase(args)
            text = "Покупка добавлена:\n\n" + _preview(ActionProposal("", proposal.intent, args, "", now, now)).replace("🛒 Добавить в покупки?\n\n", "🛒 ")
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
        else:
            raise ValueError("unsupported_proposal")
    except ValueError:
        proposal.status = "pending"
        logger.info("NL proposal domain validation failed intent=%s", proposal.intent.value, exc_info=True)
        await query.edit_message_text("Не удалось применить предложение. Проверь данные через «Изменить».", reply_markup=_keyboard(proposal.proposal_id))
        return _idle_state(context)
    proposal.status = "confirmed"
    discard_proposal(context.user_data, proposal)
    logger.info("NL proposal confirmed intent=%s", proposal.intent.value)
    await query.edit_message_text(text, reply_markup=_menu_keyboard())
    return SECTION


async def _edit_proposal(update: Update, context: ContextTypes.DEFAULT_TYPE, proposal: ActionProposal) -> int:
    query = update.callback_query
    args = proposal.arguments
    discard_proposal(context.user_data, proposal)
    if proposal.intent is IntentKind.ADD_MOVIE_OR_TV:
        await query.edit_message_text("Отправь новое название фильма или сериала:")
        return ADDING_FILM_TITLE
    if proposal.intent is IntentKind.ADD_PURCHASE:
        context.user_data.update({"purchase_title": args.get("title"), "purchase_link": args.get("link") or "",
                                  "purchase_price": args.get("price"), "purchase_priority": args.get("priority") or ""})
        await query.edit_message_text("Изменение через обычную форму. Отправь название покупки:")
        return ADDING_PURCHASE_TITLE
    if proposal.intent is IntentKind.ADD_PERSONAL_CALENDAR_EVENT:
        profile = get_allowed_profile(update) or {}
        context.user_data["calendar_owner"] = profile.get("wishlist_owner")
        await query.edit_message_text("Изменение через обычную форму. Отправь название события:")
        return ADDING_CALENDAR_EVENT_TITLE
    await query.edit_message_text("Изменение через обычную форму. Отправь название события:")
    return ADDING_EVENT_TITLE
