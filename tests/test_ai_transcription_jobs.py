from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.handlers import ai_transcription
from bot.services.aiesa_transcription import AiesaError, AiesaResult, AiesaStatus, ProviderSegment


class MemoryStorage:
    def __init__(self, job):
        self.data = {"ai_jobs": [job]}

    def load(self):
        return self.data

    def update(self, mutator):
        return mutator(self.data), self.data


class Bot:
    def __init__(self):
        self.messages = []
        self.documents = []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    async def send_document(self, chat_id, **kwargs):
        self.documents.append((chat_id, kwargs["filename"]))


def job(**changes):
    value = {
        "id": "safe-job-id", "type": "transcription", "provider": "aiesa", "actor": "vova",
        "telegram_chat_id": 123, "provider_job_id": "provider-id", "original_filename": "audio.ogg",
        "status": "processing", "created_at": "", "updated_at": "", "delivered_at": "",
        "attempts": 0, "next_attempt_at": "", "media_type": "voice", "file_size_bytes": 100,
        "duration_seconds": 10, "last_stage": "", "failure_category": "",
    }
    value.update(changes)
    return value


def context(service):
    bot = Bot()
    return SimpleNamespace(application=SimpleNamespace(bot_data={"aiesa_service": service,
        "master_transcriptions": {}}), bot=bot)


async def test_provider_timeout_exhaustion_fails_and_notifies(monkeypatch):
    class Service:
        async def status(self, provider_job_id):
            raise AiesaError("timeout", transient=True)

    stored = MemoryStorage(job(attempts=7))
    monkeypatch.setattr(ai_transcription, "storage", stored)
    ctx = context(Service())
    await ai_transcription.process_transcription_jobs(ctx)

    assert stored.data["ai_jobs"][0]["status"] == "failed"
    assert stored.data["ai_jobs"][0]["failure_category"] == "timeout"
    assert len(ctx.bot.messages) == 1
    assert "отправить ещё раз" in ctx.bot.messages[0][1]


@pytest.mark.parametrize("category", ["http_400", "malformed_result", "empty_transcript"])
async def test_definitive_provider_failures_never_stay_processing(monkeypatch, category):
    class Service:
        async def status(self, provider_job_id):
            if category == "http_400":
                raise AiesaError(category)
            return AiesaStatus(provider_job_id, "completed", 100, "https://result.example/a.json")

        async def result(self, result_url):
            if category == "malformed_result":
                raise AiesaError(category)
            return AiesaResult(1, (), 0)

    stored = MemoryStorage(job())
    monkeypatch.setattr(ai_transcription, "storage", stored)
    ctx = context(Service())
    await ai_transcription.process_transcription_jobs(ctx)

    assert stored.data["ai_jobs"][0]["status"] == "failed"
    assert stored.data["ai_jobs"][0]["failure_category"] == category
    assert len(ctx.bot.messages) == 1


async def test_docx_temp_directory_is_cleaned_when_delivery_fails(monkeypatch, tmp_path: Path):
    segment = ProviderSegment(0, "SPEAKER_01", 0, 1, "00:00:00", "00:00:01", "Привет")

    class Service:
        async def status(self, provider_job_id):
            return AiesaStatus(provider_job_id, "completed", 100, "https://result.example/a.json")

        async def result(self, result_url):
            return AiesaResult(1, (segment,), 1)

    class FailingBot(Bot):
        async def send_document(self, chat_id, **kwargs):
            raise RuntimeError("telegram unavailable")

    workdir = tmp_path / "result-workdir"
    monkeypatch.setattr(ai_transcription.tempfile, "mkdtemp", lambda **kwargs: str(workdir.mkdir() or workdir))
    stored = MemoryStorage(job())
    monkeypatch.setattr(ai_transcription, "storage", stored)
    ctx = context(Service())
    ctx.bot = FailingBot()
    await ai_transcription.process_transcription_jobs(ctx)

    assert not workdir.exists()
    assert stored.data["ai_jobs"][0]["status"] == "postprocessing"
    assert stored.data["ai_jobs"][0]["last_stage"] == "telegram_delivery"
    assert stored.data["ai_jobs"][0]["attempts"] == 1

async def test_successful_job_completes_and_delivers_docx(monkeypatch):
    segment = ProviderSegment(0, "SPEAKER_01", 0, 1, "00:00:00", "00:00:01", "Привет")

    class Service:
        async def status(self, provider_job_id):
            return AiesaStatus(provider_job_id, "completed", 100, "https://result.example/a.json", minutes_billed=1)

        async def result(self, result_url):
            return AiesaResult(1, (segment,), 1)

    stored = MemoryStorage(job())
    monkeypatch.setattr(ai_transcription, "storage", stored)
    ctx = context(Service())
    await ai_transcription.process_transcription_jobs(ctx)

    assert stored.data["ai_jobs"][0]["status"] == "completed"
    assert stored.data["ai_jobs"][0]["delivered_at"]
    assert len(ctx.bot.documents) == 1


async def test_telegram_download_failure_is_reported_and_temp_is_cleaned(monkeypatch, tmp_path: Path):
    class Media:
        file_name = "audio.ogg"
        mime_type = "audio/ogg"
        file_size = 100
        duration = 42

        async def get_file(self):
            raise RuntimeError("telegram download failed")

    class Message:
        audio = Media()
        voice = document = video = video_note = None

        def __init__(self):
            self.replies = []

        async def reply_text(self, text):
            self.replies.append(text)

    workdir = tmp_path / "download-workdir"
    monkeypatch.setattr(ai_transcription, "ensure_access", lambda update: _true())
    monkeypatch.setattr(ai_transcription.tempfile, "mkdtemp", lambda **kwargs: str(workdir.mkdir() or workdir))
    message = Message()
    update = SimpleNamespace(message=message)
    ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"aiesa_service": object(),
        "transcription_max_bytes": 1024}), bot=Bot())

    await ai_transcription.receive_media(update, ctx)

    assert any("Не получилось начать" in reply for reply in message.replies)
    assert not workdir.exists()


async def _true():
    return True
