from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://api.transcription.aiesa.ru/api/v2/transcriptions"


class AiesaError(Exception):
    """A bounded provider error safe for orchestration code."""

    def __init__(self, category: str, *, transient: bool = False):
        super().__init__(category)
        self.category = category
        self.transient = transient


@dataclass(frozen=True)
class AiesaStatus:
    id: str
    status: str
    progress: int
    result_json_url: str | None = None
    duration_seconds: float | None = None
    minutes_billed: float | None = None


@dataclass(frozen=True)
class ProviderSegment:
    id: int
    speaker: str
    start: float
    end: float
    start_time: str
    end_time: str
    text: str


@dataclass(frozen=True)
class AiesaResult:
    duration_seconds: float
    segments: tuple[ProviderSegment, ...]
    speaker_count: int


def auth_headers(public: str, secret: str, timestamp: str | None = None) -> dict[str, str]:
    stamp = timestamp or str(int(time.time()))
    signature = hmac.new(secret.encode(), f"{public}\n{stamp}".encode(), hashlib.sha256).hexdigest()
    return {"X-Public-Key": public, "X-Timestamp": stamp, "X-Signature": signature}


def is_transient_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


class AiesaTranscriptionService:
    def __init__(self, public: str, secret: str, *, client: httpx.AsyncClient | None = None, timeout: float = 120):
        if not public or not secret:
            raise ValueError("Aiesa credentials are required")
        self.public, self.secret, self.client, self.timeout = public, secret, client, timeout

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            if self.client:
                response = await self.client.request(method, url, timeout=self.timeout, **kwargs)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise AiesaError("timeout", transient=True) from exc
        except httpx.HTTPError as exc:
            raise AiesaError("network", transient=True) from exc
        if response.status_code >= 400:
            raise AiesaError(f"http_{response.status_code}", transient=is_transient_status(response.status_code))
        return response

    async def create(self, media_path: Path, filename: str, content_type: str) -> str:
        with media_path.open("rb") as media:
            response = await self._request("POST", BASE_URL, headers=auth_headers(self.public, self.secret),
                                           files={"file": (filename, media, content_type)})
        if response.status_code != 201:
            raise AiesaError("unexpected_create_status", transient=response.status_code >= 500)
        try:
            job_id = response.json()["transcription_id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise AiesaError("malformed_create_response") from exc
        if not isinstance(job_id, str) or not job_id.strip():
            raise AiesaError("malformed_create_response")
        return job_id

    async def status(self, job_id: str) -> AiesaStatus:
        response = await self._request("GET", f"{BASE_URL}/{job_id}", headers=auth_headers(self.public, self.secret))
        try:
            data = response.json()["data"]
            status = data["status"]
            identifier = data["id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise AiesaError("malformed_status_response") from exc
        if not isinstance(status, str) or not isinstance(identifier, str):
            raise AiesaError("malformed_status_response")
        results = data.get("results") if isinstance(data.get("results"), dict) else {}
        result_url = results.get("json") if isinstance(results.get("json"), str) else None
        if status == "completed" and not result_url:
            raise AiesaError("missing_result_url", transient=True)
        return AiesaStatus(identifier, status, int(data.get("progress") or 0), result_url,
                           _number(data.get("duration_seconds")), _number(data.get("minutes_billed")))

    async def result(self, result_url: str) -> AiesaResult:
        if not result_url.startswith("https://"):
            raise AiesaError("invalid_result_url")
        # The provider result is a retrieval URL; never forward API credentials to its storage host.
        response = await self._request("GET", result_url)
        try:
            transcription = response.json()["transcription"]
            raw_segments = transcription["segments"]
        except (ValueError, KeyError, TypeError) as exc:
            raise AiesaError("malformed_result") from exc
        if not isinstance(raw_segments, list):
            raise AiesaError("malformed_result")
        segments: list[ProviderSegment] = []
        for index, raw in enumerate(raw_segments):
            if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
                raise AiesaError("malformed_segment")
            try:
                start, end = float(raw["start"]), float(raw["end"])
            except (KeyError, TypeError, ValueError) as exc:
                raise AiesaError("malformed_segment") from exc
            segments.append(ProviderSegment(int(raw.get("id", index)), str(raw.get("speaker") or "UNKNOWN"),
                                             start, end, str(raw.get("start_time") or _clock(start)),
                                             str(raw.get("end_time") or _clock(end)), raw["text"].strip()))
        speakers = {segment.speaker for segment in segments}
        return AiesaResult(_number(transcription.get("duration")) or 0.0, tuple(segments), len(speakers))


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
