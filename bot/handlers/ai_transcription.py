from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.services.aiesa_transcription import AiesaError, AiesaTranscriptionService
from bot.services.polza_master_transcription import PolzaMasterTranscriptionService
from bot.services.transcript_alignment import select_best_transcript
from bot.services.transcript_docx import create_docx, duration_text, output_filename, safe_name
from bot.services.transcript_processing import PolzaTranscriptCleaner, cleanup_best_effort, normalize_segments
from bot.states import MENU, WAITING_FOR_AI_TRANSCRIPTION
from bot.storage import storage
from bot.utils import ensure_access, get_username

logger = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".mp3", ".m4a", ".mp4", ".wav", ".ogg", ".opus", ".webm", ".mov"}
TERMINAL_PROVIDER_STATUSES = {"failed", "error", "cancelled", "canceled"}


def ai_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎙 Расшифровка аудио", callback_data="aif:transcribe")],
                                 [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]])


async def ai_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return MENU
    query = update.callback_query
    await query.answer()
    if query.data == "aif:menu":
        await query.edit_message_text("🤖 AI-фичи\n\nВыбери инструмент:", reply_markup=ai_menu_keyboard())
        return MENU
    await query.edit_message_text("🎙 Пришли аудио или видео, которое нужно расшифровать.\n\n"
                                  "Можно отправлять длинные записи — обработка займёт некоторое время.",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="aif:menu")],
                                                                     [InlineKeyboardButton("🏠 В меню", callback_data="menu:main")]]))
    return WAITING_FOR_AI_TRANSCRIPTION


def media_from_message(message: Any) -> tuple[Any, str, str] | None:
    media = message.audio or message.voice or message.document or message.video or message.video_note
    if media is None:
        return None
    default_ext = ".ogg" if message.voice else ".mp4" if (message.video or message.video_note) else ""
    filename = getattr(media, "file_name", None) or f"recording{default_ext}"
    content_type = getattr(media, "mime_type", None) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        return None
    return media, safe_name(filename) + extension, content_type


async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await ensure_access(update):
        return MENU
    selected = media_from_message(update.message)
    if selected is None:
        await update.message.reply_text("Не получилось обработать этот формат файла. Пришли аудио в MP3, M4A, WAV или OGG.")
        return WAITING_FOR_AI_TRANSCRIPTION
    service: AiesaTranscriptionService | None = context.application.bot_data.get("aiesa_service")
    if service is None:
        await update.message.reply_text("Расшифровка сейчас не настроена. Попробуй ещё раз позже.")
        return MENU
    media, filename, content_type = selected
    media_type = _media_type(update.message)
    file_size_bytes = int(getattr(media, "file_size", 0) or 0)
    media_duration_seconds = int(getattr(media, "duration", 0) or 0)
    max_bytes = context.application.bot_data["transcription_max_bytes"]
    if file_size_bytes > max_bytes:
        logger.warning("AI transcription stage=validation outcome=rejected media_type=%s file_size_bytes=%s "
                       "duration_seconds=%s category=file_too_large", media_type, file_size_bytes,
                       media_duration_seconds)
        await update.message.reply_text(f"Файл слишком большой. Максимальный размер — {max_bytes // 1024 // 1024} МБ.")
        return WAITING_FOR_AI_TRANSCRIPTION
    progress = await update.message.reply_text("⏳ Обрабатываю запись…")
    workdir = Path(tempfile.mkdtemp(prefix="telegram-transcription-"))
    os.chmod(workdir, 0o700)
    media_path = workdir / filename
    started = time.monotonic()
    stage = "telegram_download"
    try:
        logger.info("AI transcription stage=telegram_download outcome=started media_type=%s file_size_bytes=%s "
                    "duration_seconds=%s", media_type, file_size_bytes, media_duration_seconds)
        telegram_file = await media.get_file()
        await telegram_file.download_to_drive(media_path)
        downloaded_size = media_path.stat().st_size
        logger.info("AI transcription stage=telegram_download outcome=success media_type=%s file_size_bytes=%s "
                    "duration_seconds=%s elapsed_seconds=%.3f", media_type, downloaded_size,
                    media_duration_seconds, time.monotonic() - started)
        if downloaded_size > max_bytes:
            logger.warning("AI transcription stage=validation outcome=rejected media_type=%s file_size_bytes=%s "
                           "duration_seconds=%s category=file_too_large", media_type, downloaded_size,
                           media_duration_seconds)
            await _resolve_progress(progress, f"⚠️ Файл слишком большой. Максимальный размер — {max_bytes // 1024 // 1024} МБ.")
            return WAITING_FOR_AI_TRANSCRIPTION
        stage = "provider_creation"
        master_service: PolzaMasterTranscriptionService | None = context.application.bot_data.get(
            "master_transcription_service")
        aiesa_call = service.create(media_path, filename, content_type)
        if master_service:
            provider_job_id, master = await asyncio.gather(
                aiesa_call, master_service.transcribe(media_path, filename, content_type))
            context.application.bot_data.setdefault("master_transcriptions", {})[provider_job_id] = master
            logger.info("AI transcription master provider=polza outcome=%s category=%s",
                        master.outcome, master.failure_category or "none")
        else:
            provider_job_id = await aiesa_call
    except Exception as exc:
        category = exc.category if isinstance(exc, AiesaError) else "download_or_creation_failure"
        logger.warning("AI transcription stage=%s outcome=failed provider=aiesa media_type=%s "
                       "file_size_bytes=%s duration_seconds=%s category=%s exception_class=%s elapsed_seconds=%.3f",
                       stage, media_type, file_size_bytes, media_duration_seconds, category, type(exc).__name__,
                       time.monotonic() - started)
        await _resolve_progress(progress, "⚠️ Не получилось начать расшифровку. Отправь запись ещё раз чуть позже.")
        return MENU
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    now = datetime.now(timezone.utc).isoformat()
    job = {"id": uuid.uuid4().hex, "type": "transcription", "provider": "aiesa", "actor": get_username(update),
           "telegram_chat_id": update.effective_chat.id, "provider_job_id": provider_job_id,
           "status_message_id": int(getattr(progress, "message_id", 0) or 0),
           "original_filename": filename, "status": "processing", "created_at": now, "updated_at": now,
           "delivered_at": "", "attempts": 0, "next_attempt_at": "", "media_type": media_type,
           "file_size_bytes": downloaded_size, "duration_seconds": media_duration_seconds,
           "last_stage": "provider_processing", "failure_category": ""}
    storage.update(lambda data: data.setdefault("ai_jobs", []).append(job))
    logger.info("AI transcription job_id=%s actor=%s stage=creation outcome=success provider=aiesa "
                "media_type=%s file_size_bytes=%s duration_seconds=%s final_job_state=processing elapsed_seconds=%.3f",
                job["id"], job["actor"], media_type, downloaded_size, media_duration_seconds,
                time.monotonic() - started)
    return MENU


async def process_transcription_jobs(context: ContextTypes.DEFAULT_TYPE) -> None:
    service: AiesaTranscriptionService | None = context.application.bot_data.get("aiesa_service")
    if service is None:
        return
    now = datetime.now(timezone.utc)
    jobs = storage.load().get("ai_jobs", [])
    for job in jobs:
        if job["status"] in {"completed", "failed"} or not _due(job, now):
            continue
        started = time.monotonic()
        stage = "provider_status"
        attempt = int(job.get("attempts") or 0) + 1
        try:
            status = await service.status(job["provider_job_id"])
            logger.info("AI transcription job_id=%s actor=%s stage=%s attempt=%s provider=aiesa "
                        "provider_status=%s elapsed_seconds=%.3f", job["id"], job["actor"], stage,
                        attempt, status.status, time.monotonic() - started)
            if status.status in TERMINAL_PROVIDER_STATUSES:
                _set_job(job["id"], status="failed", last_stage=stage,
                         failure_category=f"provider_status_{status.status}")
                await _notify_failure(context, job, f"provider_status_{status.status}")
                continue
            if status.status != "completed":
                _set_job(job["id"], attempts=0, next_attempt_at="")
                continue
            stage = "provider_result"
            _set_job(job["id"], status="postprocessing", last_stage=stage)
            result = await service.result(status.result_json_url or "")
            if not result.segments or not any(segment.text.strip() for segment in result.segments):
                raise AiesaError("empty_transcript")
            stage = "postprocessing"
            turns = normalize_segments(result.segments)
            master = context.application.bot_data.get("master_transcriptions", {}).pop(job["provider_job_id"], None)
            selection = select_best_transcript(turns, master)
            source = selection.source
            if selection.alignment is not None:
                alignment = selection.alignment
                logger.info("AI transcription alignment accepted=%s similarity_bucket=%s turns=%s boundaries=%s "
                            "unsafe_boundaries=%s local_fallback_boundaries=%s reason=%s", alignment.accepted,
                            _similarity_bucket(alignment.similarity), len(turns), len(alignment.boundaries),
                            alignment.unsafe_boundary_count,
                            alignment.local_fallback_boundary_count,
                            alignment.rejection_reason or "none")
                for boundary_index, boundary in enumerate(alignment.boundaries):
                    if boundary.confidence.value == "low":
                        logger.warning(
                            "AI transcription boundary index=%s estimated=%s selected=%s displacement=%s "
                            "left_fit=%s right_fit=%s confidence=%s margin=%s short_protected=%s reason=%s "
                            "local_fallback_applied=%s",
                            boundary_index, boundary.estimated, boundary.selected,
                            boundary.selected - boundary.estimated, boundary.left_fit_bucket,
                            boundary.right_fit_bucket, boundary.confidence.value,
                            boundary.margin_bucket, boundary.short_turn_protected,
                            boundary.reason or "none", boundary.local_fallback_applied,
                        )
                if alignment.pathological_turn is not None:
                    diagnostic = alignment.pathological_turn
                    logger.warning(
                        "AI transcription pathological_turn index=%s source_tokens=%s hybrid_tokens=%s "
                        "ratio=%.2f ratio_limit=%.2f absolute_slack=%s token_limit=%.2f",
                        diagnostic.index, diagnostic.source_tokens, diagnostic.hybrid_tokens,
                        diagnostic.ratio, diagnostic.ratio_limit, diagnostic.absolute_slack,
                        diagnostic.token_limit,
                    )
            turns = list(selection.turns)
            cleaner_obj = context.application.bot_data.get("transcript_cleaner")
            cleaner = cleaner_obj.clean_chunk if cleaner_obj else None
            turns, cleanup = await cleanup_best_effort(turns, cleaner)
            stage = "docx_generation"
            workdir = Path(tempfile.mkdtemp(prefix="telegram-transcription-result-"))
            os.chmod(workdir, 0o700)
            try:
                filename = output_filename(job["original_filename"])
                docx_path = workdir / filename
                create_docx(docx_path, original_filename=job["original_filename"], processed_at=now.replace(tzinfo=None),
                            duration_seconds=result.duration_seconds, speaker_count=result.speaker_count, turns=turns)
                stage = "telegram_delivery"
                _set_job(job["id"], status="delivering", last_stage=stage)
                caption = (f"✅ Расшифровка готова\n\n🎙 {job['original_filename']}\n"
                           f"⏱ {duration_text(result.duration_seconds)}\n👥 Спикеров: {result.speaker_count}")
                with docx_path.open("rb") as document:
                    await context.bot.send_document(job["telegram_chat_id"], document=document, filename=filename, caption=caption)
                _set_job(job["id"], status="completed", delivered_at=datetime.now(timezone.utc).isoformat(), attempts=0)
                await _clear_job_progress(context, job)
                logger.info("AI transcription job_id=%s actor=%s stage=completed attempt=%s provider=aiesa "
                            "duration_seconds=%s minutes_billed=%s speaker_count=%s segment_count=%s source=%s "
                            "cleanup=%s delivery=success final_job_state=completed elapsed_seconds=%.3f", job["id"],
                            job["actor"], attempt, result.duration_seconds,
                            status.minutes_billed, result.speaker_count, len(result.segments), source, cleanup,
                            time.monotonic() - started)
            finally:
                shutil.rmtree(workdir, ignore_errors=True)
        except AiesaError as exc:
            if exc.transient:
                exhausted = _retry(job, now, stage=stage, category=exc.category)
                if exhausted:
                    await _notify_failure(context, job, exc.category)
            else:
                _set_job(job["id"], status="failed", last_stage=stage, failure_category=exc.category)
                await _notify_failure(context, job, exc.category)
            logger.warning("AI transcription job_id=%s actor=%s stage=%s attempt=%s provider=aiesa category=%s "
                           "transient=%s exception_class=%s elapsed_seconds=%.3f", job["id"], job["actor"], stage,
                           attempt, exc.category, exc.transient, type(exc).__name__, time.monotonic() - started)
        except Exception as exc:
            exhausted = _retry(job, now, stage=stage, category="internal")
            if exhausted:
                await _notify_failure(context, job, "internal")
            logger.exception("AI transcription job_id=%s actor=%s stage=%s attempt=%s provider=aiesa "
                             "category=internal exception_class=%s elapsed_seconds=%.3f", job["id"], job["actor"],
                             stage, attempt, type(exc).__name__, time.monotonic() - started)


def _due(job: dict[str, Any], now: datetime) -> bool:
    try:
        return not job.get("next_attempt_at") or datetime.fromisoformat(job["next_attempt_at"]) <= now
    except ValueError:
        return True


def _similarity_bucket(value: float) -> str:
    if value >= 0.85:
        return "high"
    if value >= 0.65:
        return "medium"
    return "low"


def _set_job(job_id: str, **changes: Any) -> None:
    changes["updated_at"] = datetime.now(timezone.utc).isoformat()
    def mutate(data: dict[str, Any]) -> None:
        for item in data.get("ai_jobs", []):
            if item["id"] == job_id:
                item.update(changes)
                break
    storage.update(mutate)


def _retry(job: dict[str, Any], now: datetime, *, stage: str, category: str) -> bool:
    attempts = int(job.get("attempts") or 0) + 1
    if attempts >= 8:
        _set_job(job["id"], status="failed", attempts=attempts, next_attempt_at="", last_stage=stage,
                 failure_category=category)
        return True
    target_status = "processing" if stage == "provider_status" else "postprocessing"
    _set_job(job["id"], status=target_status,
             attempts=attempts, next_attempt_at=(now + timedelta(seconds=min(900, 15 * 2 ** attempts))).isoformat(),
             last_stage=stage, failure_category=category)
    return False


async def _notify_failure(context: ContextTypes.DEFAULT_TYPE, job: dict[str, Any], category: str) -> None:
    try:
        text = "⚠️ Не получилось расшифровать эту запись. Отправь аудио ещё раз."
        message_id = int(job.get("status_message_id") or 0)
        if message_id:
            await context.bot.edit_message_text(text, chat_id=job["telegram_chat_id"], message_id=message_id)
        else:
            await context.bot.send_message(job["telegram_chat_id"], text)
        outcome = "success"
    except Exception as exc:
        outcome = "failed"
        logger.exception("AI transcription job_id=%s actor=%s stage=failure_notification outcome=failed "
                         "category=%s exception_class=%s final_job_state=failed", job["id"], job["actor"],
                         category, type(exc).__name__)
    logger.info("AI transcription job_id=%s actor=%s stage=failure_notification outcome=%s category=%s "
                "final_job_state=failed", job["id"], job["actor"], outcome, category)


async def _resolve_progress(message: Any, text: str) -> None:
    """Resolve one operation status without exposing Telegram failures to users."""
    try:
        await message.edit_text(text)
    except Exception:
        logger.warning("AI transcription stage=status_resolution outcome=failed", exc_info=True)


async def _clear_job_progress(context: ContextTypes.DEFAULT_TYPE, job: dict[str, Any]) -> None:
    message_id = int(job.get("status_message_id") or 0)
    if not message_id:
        return
    try:
        await context.bot.delete_message(chat_id=job["telegram_chat_id"], message_id=message_id)
    except Exception:
        logger.warning("AI transcription job_id=%s actor=%s stage=status_resolution outcome=failed",
                       job["id"], job["actor"], exc_info=True)


def _media_type(message: Any) -> str:
    for name in ("audio", "voice", "document", "video", "video_note"):
        if getattr(message, name, None) is not None:
            return name
    return "unknown"
