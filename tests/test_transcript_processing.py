import asyncio
from datetime import datetime
from pathlib import Path
import zipfile

import pytest
from bot.services.aiesa_transcription import ProviderSegment
from bot.services.transcript_docx import create_docx, output_filename
from bot.services.transcript_processing import (TranscriptTurn, chunk_turns, cleanup_best_effort,
                                                 normalize_segments, speaker_label, validate_cleaned)
from bot.storage import JsonStorage


def segment(id, speaker, start, end, text):
    return ProviderSegment(id, speaker, start, end, f"00:00:{int(start):02d}", f"00:00:{int(end):02d}", text)


def test_speaker_mapping_one_two_and_many():
    assert [speaker_label(f"SPEAKER_{n:02d}") for n in (1, 2, 12)] == ["Спикер 1", "Спикер 2", "Спикер 12"]
    assert speaker_label("") == "Спикер неизвестен"


def test_normalization_orders_merges_without_losing_text_and_preserves_overlap():
    turns = normalize_segments([segment(3, "SPEAKER_02", 5, 7, "два"),
                                segment(1, "SPEAKER_01", 0, 2, "один"),
                                segment(2, "SPEAKER_01", 2.5, 4, "ещё"),
                                segment(4, "SPEAKER_02", 6, 8, "перекрытие"),
                                segment(5, "SPEAKER_01", 9, 10, "")])
    assert [turn.text for turn in turns] == ["один ещё", "два", "перекрытие"]
    assert [turn.speaker for turn in turns] == ["Спикер 1", "Спикер 2", "Спикер 2"]
    assert "один" in turns[0].text and "ещё" in turns[0].text


def test_chunking_never_splits_turns():
    turns = [TranscriptTurn(i, "Спикер 1", i, i + 1, f"00:00:{i:02d}", "x" * 80) for i in range(1, 8)]
    chunks = chunk_turns(turns, max_chars=250)
    assert [turn.id for chunk in chunks for turn in chunk] == list(range(1, 8))
    assert len(chunks) > 1


def test_cleanup_validation_rejects_missing_extra_identity_and_timestamp():
    turn = TranscriptTurn(1, "Спикер 1", 0, 1, "00:00:00", "текст")
    valid = {"segments": [{"id": 1, "speaker": "Спикер 1", "timestamp": "00:00:00", "text": "Текст."}]}
    assert validate_cleaned([turn], valid)[0].text == "Текст."
    for invalid in ({"segments": []}, {"segments": valid["segments"] * 2},
                    {"segments": [{**valid["segments"][0], "speaker": "Спикер 2"}]},
                    {"segments": [{**valid["segments"][0], "timestamp": "00:00:01"}]}):
        with pytest.raises(ValueError):
            validate_cleaned([turn], invalid)


async def test_partial_and_total_cleanup_fallback():
    turns = [TranscriptTurn(i, "Спикер 1", i, i + 1, f"00:00:{i:02d}", f"raw {i}") for i in range(1, 4)]
    calls = 0
    async def partial(chunk):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [TranscriptTurn(**{**turn.__dict__, "text": turn.text.upper()}) for turn in chunk]
        raise ValueError
    result, outcome = await cleanup_best_effort(turns, partial, max_chars=110)
    assert outcome == "partial" and result[0].text == "RAW 1" and result[-1].text == "raw 3"
    async def failed(chunk):
        raise ValueError
    result, outcome = await cleanup_best_effort(turns, failed, max_chars=110)
    assert outcome == "failed" and result == turns


def test_durable_job_normalization_and_docx_cyrillic(tmp_path: Path):
    store = JsonStorage(tmp_path / "data.json")
    data = store.default_data()
    data["ai_jobs"] = [{"id": "id", "type": "transcription", "provider": "aiesa", "actor": "wp_bvv",
                        "telegram_chat_id": 1, "provider_job_id": "provider", "original_filename": "секрет.mp3",
                        "status": "processing", "created_at": "now", "updated_at": "now"}]
    store.save(data)
    assert store.load()["ai_jobs"][0]["provider_job_id"] == "provider"
    turn = TranscriptTurn(1, "Спикер 1", 0, 1, "00:00:00", "Привет, мир")
    path = tmp_path / "out.docx"
    create_docx(path, original_filename="встреча.mp3", processed_at=datetime(2026, 8, 24, 16, 30),
                duration_seconds=61, speaker_count=1, turns=[turn])
    with zipfile.ZipFile(path) as archive:
        text = archive.read("word/document.xml").decode()
    assert "Расшифровка" in text and "Спикер 1" in text and "00:00:00" in text and "Привет, мир" in text
    assert output_filename("../../опасный / файл.mp3", datetime(2026, 8, 24)).endswith("2026-08-24.docx")
