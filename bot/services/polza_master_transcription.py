from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import logging
import mimetypes
from pathlib import Path
import re
from typing import Literal

import httpx

POLZA_AUDIO_TRANSCRIPTIONS_URL = "https://polza.ai/api/v1/audio/transcriptions"
_SAFE_AUDIO_MIME = re.compile(r"^audio/[A-Za-z0-9!#$&^_.+-]+$")
_DEFAULT_AUDIO_MIME = "audio/ogg"
_M4A_MIME_NORMALIZATION = {
    "audio/m4a": "audio/mp4",
    "audio/x-m4a": "audio/mp4",
    "video/mp4": "audio/mp4",
}
logger = logging.getLogger(__name__)


def _audio_mime_type(content_type: str | None, filename: str) -> str:
    """Return a Data URL-safe audio MIME type without trusting Telegram metadata."""
    candidate = content_type.strip() if isinstance(content_type, str) else ""
    if Path(filename).suffix.lower() == ".m4a" and candidate.lower() == "audio/mpeg":
        return "audio/mp4"
    normalized = _M4A_MIME_NORMALIZATION.get(candidate.lower())
    if normalized:
        return normalized
    if _SAFE_AUDIO_MIME.fullmatch(candidate):
        return candidate.lower()
    guessed, _ = mimetypes.guess_type(filename)
    if guessed and _SAFE_AUDIO_MIME.fullmatch(guessed):
        return guessed.lower()
    return _DEFAULT_AUDIO_MIME


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

    async def transcribe(self, media_path: Path, filename: str, content_type: str | None,
                         *, language: str = "ru") -> MasterTranscriptionResult:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            # Polza's audio endpoint accepts a base64 Data URL, not a multipart upload. Keep
            # the encoding request-local and never expose it through results or diagnostics.
            mime_type = _audio_mime_type(content_type, filename)
            data_url = (f"data:{mime_type};base64,"
                        + base64.b64encode(media_path.read_bytes()).decode("ascii"))
            payload = {
                "model": self.model,
                "file": data_url,
                "language": language,
                "response_format": "json",
            }
            request_body_bytes = len(json.dumps(payload, ensure_ascii=False,
                                                separators=(",", ":")).encode("utf-8"))
            if self.client:
                response = await self.client.post(POLZA_AUDIO_TRANSCRIPTIONS_URL, headers=headers,
                                                  json=payload, timeout=self.timeout)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(POLZA_AUDIO_TRANSCRIPTIONS_URL, headers=headers,
                                                 json=payload)
            logger.info("Polza master request mime=%s body_bytes=%s status=%s",
                        mime_type, request_body_bytes, response.status_code)
            del data_url, payload
            response.raise_for_status()
            response_payload = response.json()
            text = response_payload.get("text") if isinstance(response_payload, dict) else None
            if not isinstance(text, str) or not text.strip():
                logger.info("Polza master provider outcome=failed category=malformed_response")
                return MasterTranscriptionResult("failed", self.model, failure_category="malformed_response")
            logger.info("Polza master provider outcome=success category=none")
            return MasterTranscriptionResult("success", self.model, text=text.strip())
        except httpx.TimeoutException:
            logger.info("Polza master provider outcome=failed category=timeout")
            return MasterTranscriptionResult("failed", self.model, failure_category="timeout")
        except httpx.HTTPStatusError as exc:
            category = "rate_limited" if exc.response.status_code == 429 else (
                "provider_unavailable" if exc.response.status_code >= 500
                else f"provider_rejected_http_{exc.response.status_code}")
            logger.info("Polza master provider outcome=failed category=%s", category)
            return MasterTranscriptionResult("failed", self.model, failure_category=category)
        except (httpx.HTTPError, OSError):
            logger.info("Polza master provider outcome=failed category=network")
            return MasterTranscriptionResult("failed", self.model, failure_category="network")
        except (ValueError, TypeError):
            logger.info("Polza master provider outcome=failed category=malformed_response")
            return MasterTranscriptionResult("failed", self.model, failure_category="malformed_response")
