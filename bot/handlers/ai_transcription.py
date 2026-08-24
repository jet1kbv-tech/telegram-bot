from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import shutil
import tempfile
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
    max_bytes = context.application.bot_data["transcription_max_bytes"]
    if media.file_size and media.file_size > max_bytes:
        await update.message.reply_text(f"Файл слишком большой. Максимальный размер — {max_bytes // 1024 // 1024} МБ.")
        return WAITING_FOR_AI_TRANSCRIPTION
    await update.message.reply_text("⏳ Загружаю и расшифровываю запись…")
    workdir = Path(tempfile.mkdtemp(prefix="telegram-transcription-"))
    os.chmod(workdir, 0o700)
    media_path = workdir / filename
    try:
        telegram_file = await media.get_file()
        await telegram_file.download_to_drive(media_path)
        if media_path.stat().st_size > max_bytes:
            await update.message.reply_text(f"Файл слишком большой. Максимальный размер — {max_bytes // 1024 // 1024} МБ.")
            return WAITING_FOR_AI_TRANSCRIPTION
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
    except (AiesaError, OSError):
        logger.warning("AI transcription creation failed provider=aiesa category=bounded")
        await update.message.reply_text("Не получилось начать расшифровку. Попробуй ещё раз чуть позже.")
        return MENU
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    now = datetime.now(timezone.utc).isoformat()
    job = {"id": uuid.uuid4().hex, "type": "transcription", "provider": "aiesa", "actor": get_username(update),
           "telegram_chat_id": update.effective_chat.id, "provider_job_id": provider_job_id,
           "original_filename": filename, "status": "processing", "created_at": now, "updated_at": now,
           "delivered_at": "", "attempts": 0, "next_attempt_at": ""}
    storage.update(lambda data: data.setdefault("ai_jobs", []).append(job))
    logger.info("AI transcription created provider=aiesa")
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
        try:
            status = await service.status(job["provider_job_id"])
            logger.info("AI transcription polling provider=aiesa status=%s", status.status)
            if status.status in TERMINAL_PROVIDER_STATUSES:
                _set_job(job["id"], status="failed")
                await context.bot.send_message(job["telegram_chat_id"], "Не получилось расшифровать эту запись.")
                continue
            if status.status != "completed":
                _set_job(job["id"], attempts=0, next_attempt_at="")
                continue
            _set_job(job["id"], status="postprocessing")
            result = await service.result(status.result_json_url or "")
            turns = normalize_segments(result.segments)
            master = context.application.bot_data.get("master_transcriptions", {}).pop(job["provider_job_id"], None)
            selection = select_best_transcript(turns, master)
            source = selection.source
            if selection.alignment is not None:
                alignment = selection.alignment
                unsafe = sum(boundary.confidence.value == "low" for boundary in alignment.boundaries)
                logger.info("AI transcription alignment accepted=%s similarity_bucket=%s turns=%s boundaries=%s "
                            "unsafe_boundaries=%s reason=%s", alignment.accepted,
                            _similarity_bucket(alignment.similarity), len(turns), len(alignment.boundaries),
                            unsafe, alignment.rejection_reason or "none")
            turns = list(selection.turns)
            cleaner_obj = context.application.bot_data.get("transcript_cleaner")
            cleaner = cleaner_obj.clean_chunk if cleaner_obj else None
            turns, cleanup = await cleanup_best_effort(turns, cleaner)
            workdir = Path(tempfile.mkdtemp(prefix="telegram-transcription-result-"))
            os.chmod(workdir, 0o700)
            try:
                filename = output_filename(job["original_filename"])
                docx_path = workdir / filename
                create_docx(docx_path, original_filename=job["original_filename"], processed_at=now.replace(tzinfo=None),
                            duration_seconds=result.duration_seconds, speaker_count=result.speaker_count, turns=turns)
                _set_job(job["id"], status="delivering")
                caption = (f"✅ Расшифровка готова\n\n🎙 {job['original_filename']}\n"
                           f"⏱ {duration_text(result.duration_seconds)}\n👥 Спикеров: {result.speaker_count}")
                with docx_path.open("rb") as document:
                    await context.bot.send_document(job["telegram_chat_id"], document=document, filename=filename, caption=caption)
                _set_job(job["id"], status="completed", delivered_at=datetime.now(timezone.utc).isoformat(), attempts=0)
                logger.info("AI transcription completed provider=aiesa duration_seconds=%s minutes_billed=%s speaker_count=%s "
                            "segment_count=%s source=%s cleanup=%s delivery=success", result.duration_seconds,
                            status.minutes_billed, result.speaker_count, len(result.segments), source, cleanup)
            finally:
                shutil.rmtree(workdir, ignore_errors=True)
        except AiesaError as exc:
            if exc.transient:
                _retry(job, now)
            else:
                _set_job(job["id"], status="failed")
                await context.bot.send_message(job["telegram_chat_id"], "Не получилось расшифровать эту запись.")
            logger.warning("AI transcription provider failure provider=aiesa category=%s transient=%s", exc.category, exc.transient)
        except Exception:
            _retry(job, now)
            logger.exception("AI transcription processing failure provider=aiesa category=internal")


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


def _retry(job: dict[str, Any], now: datetime) -> None:
    attempts = int(job.get("attempts") or 0) + 1
    if attempts >= 8:
        _set_job(job["id"], status="failed", attempts=attempts)
        return
    _set_job(job["id"], status="postprocessing" if job.get("status") in {"postprocessing", "delivering"} else "processing",
             attempts=attempts, next_attempt_at=(now + timedelta(seconds=min(900, 15 * 2 ** attempts))).isoformat())
