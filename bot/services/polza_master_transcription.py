from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

POLZA_AUDIO_TRANSCRIPTIONS_URL = "https://polza.ai/api/v1/audio/transcriptions"


@dataclass(frozen=True)
class MasterTranscriptionResult:
    """Content-minimal result returned to orchestration; raw payloads never escape."""

    outcome: Literal["success", "failed"]
    model: str
    text: str = ""
    failure_category: str | None = None


class PolzaMasterTranscriptionService:
    def __init__(self, api_key: str, model: str, *, timeout: float = 120,
                 client: httpx.AsyncClient | None = None):
        if not api_key or not model:
            raise ValueError("Polza master transcription configuration is required")
        self.api_key, self.model, self.timeout, self.client = api_key, model, timeout, client

    async def transcribe(self, media_path: Path, filename: str, content_type: str,
                         *, language: str = "ru") -> MasterTranscriptionResult:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = {"model": self.model, "language": language, "response_format": "json"}
        try:
            with media_path.open("rb") as media:
                files = {"file": (filename, media, content_type)}
                if self.client:
                    response = await self.client.post(POLZA_AUDIO_TRANSCRIPTIONS_URL, headers=headers,
                                                      data=data, files=files, timeout=self.timeout)
                else:
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        response = await client.post(POLZA_AUDIO_TRANSCRIPTIONS_URL, headers=headers,
                                                     data=data, files=files)
            response.raise_for_status()
            payload = response.json()
            text = payload.get("text") if isinstance(payload, dict) else None
            if not isinstance(text, str) or not text.strip():
                return MasterTranscriptionResult("failed", self.model, failure_category="malformed_response")
            return MasterTranscriptionResult("success", self.model, text=text.strip())
        except httpx.TimeoutException:
            return MasterTranscriptionResult("failed", self.model, failure_category="timeout")
        except httpx.HTTPStatusError as exc:
            category = "rate_limited" if exc.response.status_code == 429 else (
                "provider_unavailable" if exc.response.status_code >= 500 else "provider_rejected")
            return MasterTranscriptionResult("failed", self.model, failure_category=category)
        except (httpx.HTTPError, OSError):
            return MasterTranscriptionResult("failed", self.model, failure_category="network")
        except (ValueError, TypeError):
            return MasterTranscriptionResult("failed", self.model, failure_category="malformed_response")
