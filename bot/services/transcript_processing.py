from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Awaitable, Callable

import httpx

from bot.services.aiesa_transcription import ProviderSegment

POLZA_URL = "https://polza.ai/api/v1/chat/completions"
SPEAKER_RE = re.compile(r"^SPEAKER_(\d+)$", re.I)


@dataclass(frozen=True)
class TranscriptTurn:
    id: int
    speaker: str
    start: float
    end: float
    timestamp: str
    text: str


def speaker_label(provider_label: str) -> str:
    match = SPEAKER_RE.fullmatch(provider_label.strip())
    return f"Спикер {int(match.group(1))}" if match else "Спикер неизвестен"


def normalize_segments(segments: tuple[ProviderSegment, ...] | list[ProviderSegment], *, merge_gap: float = 2.0) -> list[TranscriptTurn]:
    """Sort by numeric start/end/id and merge only non-overlapping adjacent same-speaker turns."""
    ordered = sorted((s for s in segments if s.text.strip()), key=lambda s: (s.start, s.end, s.id))
    result: list[TranscriptTurn] = []
    for segment in ordered:
        turn = TranscriptTurn(len(result) + 1, speaker_label(segment.speaker), segment.start, segment.end,
                              segment.start_time, segment.text.strip())
        previous = result[-1] if result else None
        if (previous and previous.speaker == turn.speaker and turn.start >= previous.end
                and turn.start - previous.end <= merge_gap):
            result[-1] = replace(previous, end=max(previous.end, turn.end), text=f"{previous.text} {turn.text}".strip())
        else:
            result.append(turn)
    return [replace(turn, id=index) for index, turn in enumerate(result, 1)]


def chunk_turns(turns: list[TranscriptTurn], max_chars: int = 12000) -> list[list[TranscriptTurn]]:
    chunks: list[list[TranscriptTurn]] = []
    current: list[TranscriptTurn] = []
    size = 0
    for turn in turns:
        estimate = len(turn.text) + 100
        if current and size + estimate > max_chars:
            chunks.append(current)
            current, size = [], 0
        current.append(turn)
        size += estimate
    if current:
        chunks.append(current)
    return chunks


def validate_cleaned(chunk: list[TranscriptTurn], payload: object) -> list[TranscriptTurn]:
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise ValueError("invalid_cleanup_output")
    output = payload["segments"]
    if len(output) != len(chunk):
        raise ValueError("segment_count_changed")
    cleaned: list[TranscriptTurn] = []
    for original, item in zip(chunk, output, strict=True):
        if not isinstance(item, dict) or item.get("id") != original.id or item.get("speaker") != original.speaker:
            raise ValueError("segment_identity_changed")
        if item.get("timestamp") != original.timestamp or not isinstance(item.get("text"), str) or not item["text"].strip():
            raise ValueError("segment_content_invalid")
        cleaned.append(replace(original, text=item["text"].strip()))
    return cleaned


class PolzaTranscriptCleaner:
    def __init__(self, api_key: str, model: str, *, timeout: float = 60, client: httpx.AsyncClient | None = None):
        self.api_key, self.model, self.timeout, self.client = api_key, model, timeout, client

    async def clean_chunk(self, chunk: list[TranscriptTurn]) -> list[TranscriptTurn]:
        source = {"segments": [{"id": t.id, "speaker": t.speaker, "timestamp": t.timestamp, "text": t.text} for t in chunk]}
        prompt = ("Ты редактор ASR, не суммаризатор. Исправь только пунктуацию, регистр и очевидные ошибки. "
                  "Не удаляй, не добавляй, не переводи и не меняй id/speaker/timestamp. Верни только JSON "
                  "в том же формате. При сомнении сохрани исходное слово.\n" + json.dumps(source, ensure_ascii=False))
        body = {"model": self.model, "stream": False, "temperature": 0,
                "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}
        try:
            if self.client:
                response = await self.client.post(POLZA_URL, headers=self._headers(), json=body, timeout=self.timeout)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(POLZA_URL, headers=self._headers(), json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return validate_cleaned(chunk, json.loads(content))
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("cleanup_failed") from exc

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}


async def cleanup_best_effort(turns: list[TranscriptTurn], cleaner: Callable[[list[TranscriptTurn]], Awaitable[list[TranscriptTurn]]] | None,
                              *, max_chars: int = 12000) -> tuple[list[TranscriptTurn], str]:
    if cleaner is None or not turns:
        return turns, "failed"
    result: list[TranscriptTurn] = []
    failures = 0
    for chunk in chunk_turns(turns, max_chars):
        try:
            result.extend(await cleaner(chunk))
        except (ValueError, httpx.HTTPError):
            failures += 1
            result.extend(chunk)
    return result, "success" if not failures else ("failed" if failures == len(chunk_turns(turns, max_chars)) else "partial")
