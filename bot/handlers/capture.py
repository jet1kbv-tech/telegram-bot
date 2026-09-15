"""Telegram orchestration for the AI-01A text-to-Notes capture slice."""
from __future__ import annotations

import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import AI_PROPOSAL_TTL_SECONDS, BOT_TIMEZONE
from bot.services.capture import (CaptureClassifier, CaptureContext, CaptureValidationError,
                                  NotesCaptureClassifier, normalize_text_capture,
                                  validate_capture_classification)
from bot.services.capture_proposals import (CaptureProposal, create_capture_proposal,
                                            discard_capture_proposal, get_capture_proposal)
from bot.services.nl_dates import zoned_now
from bot.services.notes_service import NotesService
from bot.states import MENU, SECTION
from bot.storage import storage
from bot.utils import ensure_access, get_allowed_profile, get_username


logger = logging.getLogger(__name__)
_classifier: CaptureClassifier = NotesCaptureClassifier()
_notes_service = NotesService(storage)

_DESTINATIONS = (
    ("films", "🎬 Фильмы"), ("wishlist", "🎁 Вишлист"),
    ("purchases", "🛒 Покупки"), ("leisure", "✨ Досуг"),
    ("places", "📍 Места"), ("afisha", "🗓 Афиша"), ("notes", "📝 Заметки"),
)


def configure_capture(*, classifier: CaptureClassifier | None = None,
                      notes_service: NotesService | None = None) -> None:
    global _classifier, _notes_service
    if classifier is not None:
        _classifier = classifier
    if notes_service is not None:
        _notes_service = notes_service


def _idle_state(context: ContextTypes.DEFAULT_TYPE) -> int:
    return SECTION if context.user_data.get("active_section") else MENU


def _preview_text(proposal: CaptureProposal) -> str:
    return ("🧠 Похоже, это заметка\n\n"
            f"📝 {proposal.classification.candidate_text}\n\n"
            "Заметка будет видна только тебе.\n"
            "Пока ничего не сохранено.")


def _preview_keyboard(proposal_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Сохранить в заметки", callback_data=f"cap:confirm:{proposal_id}")],
        [InlineKeyboardButton("📂 Другой раздел", callback_data=f"cap:choose:{proposal_id}")],
        [InlineKeyboardButton("❌ Отменить", callback_data=f"cap:cancel:{proposal_id}")],
    ])


def _chooser_keyboard(proposal_id: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(label, callback_data=f"cap:dest:{destination}:{proposal_id}")]
            for destination, label in _DESTINATIONS]
    rows += [[InlineKeyboardButton("⬅️ Назад", callback_data=f"cap:back:{proposal_id}")],
             [InlineKeyboardButton("❌ Отменить", callback_data=f"cap:cancel:{proposal_id}")]]
    return InlineKeyboardMarkup(rows)


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]])


async def begin_text_capture(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
                             response: Any) -> int | None:
    """Return None on fail-closed validation so NL can retain its prior help UX."""
    logger.info("capture_received kind=text")
    try:
        source = normalize_text_capture(text)
        raw = await _classifier.classify(source, CaptureContext(
            local_now=zoned_now(BOT_TIMEZONE), timezone=BOT_TIMEZONE))
        classification = validate_capture_classification(raw, source=source)
        profile = get_allowed_profile(update) or {}
        owner = str(profile.get("wishlist_owner") or "")
        actor = get_username(update)
        if owner not in {"vova", "sasha"} or not actor:
            raise CaptureValidationError("invalid_actor")
    except Exception:
        logger.info("capture_classification_failed kind=text")
        return None
    logger.info("capture_classified destination=notes action=create")
    proposal = create_capture_proposal(
        context.user_data, actor_key=actor, owner_key=owner, source=source,
        classification=classification, now=zoned_now(BOT_TIMEZONE),
        ttl_seconds=AI_PROPOSAL_TTL_SECONDS)
    logger.info("capture_proposal_created destination=notes")
    await response.reply_text(_preview_text(proposal), reply_markup=_preview_keyboard(proposal.proposal_id))
    return _idle_state(context)


async def _safe_edit(query: Any, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except Exception:
        logger.warning("capture_callback_edit_failed")
        if getattr(query, "message", None) is not None:
            await query.message.reply_text(text, reply_markup=reply_markup)


async def capture_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return ConversationHandler.END
    query = update.callback_query
    if query is None:
        return _idle_state(context)
    await query.answer()
    parts = str(query.data or "").split(":")
    if len(parts) == 3 and parts[1] in {"confirm", "choose", "cancel", "back"}:
        action, proposal_id = parts[1], parts[2]
        destination = None
    elif len(parts) == 4 and parts[1] == "dest":
        action, destination, proposal_id = "dest", parts[2], parts[3]
    else:
        await _safe_edit(query, "Это предложение недоступно. Отправь текст ещё раз.", _menu_keyboard())
        return _idle_state(context)
    proposal = get_capture_proposal(context.user_data, proposal_id,
                                    actor_key=get_username(update), now=zoned_now(BOT_TIMEZONE))
    if proposal is None or proposal.status != "pending":
        logger.info("capture_proposal_expired_or_stale")
        await _safe_edit(query, "Это предложение уже устарело. Отправь текст ещё раз.", _menu_keyboard())
        return _idle_state(context)
    if action == "cancel":
        proposal.status = "cancelled"
        discard_capture_proposal(context.user_data, proposal)
        logger.info("capture_proposal_cancelled destination=notes")
        await _safe_edit(query, "Отменено. Ничего не сохранено.", _menu_keyboard())
        return _idle_state(context)
    if action == "choose":
        await _safe_edit(query, "Куда сохранить?\n\nВ AI-01A подключены только заметки.",
                         _chooser_keyboard(proposal.proposal_id))
        return _idle_state(context)
    if action == "back" or (action == "dest" and destination == "notes"):
        await _safe_edit(query, _preview_text(proposal), _preview_keyboard(proposal.proposal_id))
        return _idle_state(context)
    if action == "dest":
        known = {key for key, _ in _DESTINATIONS}
        text = ("Этот раздел подключим на следующем этапе Universal Capture."
                if destination in known else "Этот раздел недоступен.")
        await _safe_edit(query, text, InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ К выбору", callback_data=f"cap:choose:{proposal.proposal_id}")],
            [InlineKeyboardButton("❌ Отменить", callback_data=f"cap:cancel:{proposal.proposal_id}")],
        ]))
        return _idle_state(context)
    if action != "confirm":
        await _safe_edit(query, "Это предложение недоступно. Отправь текст ещё раз.", _menu_keyboard())
        return _idle_state(context)

    profile = get_allowed_profile(update) or {}
    owner = str(profile.get("wishlist_owner") or "")
    if owner not in {"vova", "sasha"} or owner != proposal.owner_key:
        await _safe_edit(query, "Это предложение недоступно этому пользователю.", _menu_keyboard())
        return _idle_state(context)
    proposal.status = "executing"
    try:
        _notes_service.create_note(owner=owner, text=proposal.classification.candidate_text,
                                   source="universal_capture")
    except Exception:
        proposal.status = "failed"
        discard_capture_proposal(context.user_data, proposal)
        logger.warning("capture_confirmation_failed destination=notes")
        await _safe_edit(query, "Не удалось сохранить заметку. Проверь заметки и попробуй ещё раз.",
                         _menu_keyboard())
        return _idle_state(context)
    proposal.status = "confirmed"
    discard_capture_proposal(context.user_data, proposal)
    logger.info("capture_proposal_confirmed destination=notes")
    await _safe_edit(query, "✅ Сохранил в твои заметки.", _menu_keyboard())
    return SECTION
