import base64
import json
from pathlib import Path

import httpx
import pytest

from bot.services.polza_master_transcription import (POLZA_AUDIO_TRANSCRIPTIONS_URL,
                                                     PolzaMasterTranscriptionService)


MODEL = "openai/gpt-4o-mini-transcribe"


async def _transcribe(tmp_path: Path, handler, *, content_type="audio/ogg", filename="private.ogg",
                      audio=b"private audio"):
    path = tmp_path / "media"
    path.write_bytes(audio)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return await PolzaMasterTranscriptionService("secret-key", MODEL, client=client).transcribe(
            path, filename, content_type)
    finally:
        await client.aclose()


async def test_master_transcription_json_data_url_contract(tmp_path: Path):
    audio = b"\x00audio\xff"

    def handler(request: httpx.Request):
        assert str(request.url) == POLZA_AUDIO_TRANSCRIPTIONS_URL
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer secret-key"
        assert request.headers["content-type"] == "application/json"
        payload = json.loads(request.content)
        assert payload == {
            "model": MODEL,
            "file": "data:audio/ogg;base64," + base64.b64encode(audio).decode("ascii"),
            "language": "ru",
            "response_format": "json",
        }
        return httpx.Response(200, json={"text": "  Привет, мир.  "})

    result = await _transcribe(tmp_path, handler, audio=audio)
    assert result.outcome == "success"
    assert result.model == MODEL
    assert result.text == "Привет, мир."


@pytest.mark.parametrize(("content_type", "filename", "expected"), [
    ("audio/mp4", "recording.bin", "audio/mp4"),
    ("", "recording.mp3", "audio/mpeg"),
    ("application/octet-stream", "recording.unknown", "audio/ogg"),
    ("audio/ogg;evil=1", "recording.unknown", "audio/ogg"),
])
async def test_master_transcription_mime_handling(tmp_path: Path, content_type, filename, expected):
    def handler(request: httpx.Request):
        assert json.loads(request.content)["file"].startswith(f"data:{expected};base64,")
        return httpx.Response(200, json={"text": "ok"})

    assert (await _transcribe(tmp_path, handler, content_type=content_type, filename=filename)).outcome == "success"


@pytest.mark.parametrize(("status", "category"), [
    (400, "provider_rejected_http_400"),
    (429, "rate_limited"),
    (503, "provider_unavailable"),
])
async def test_master_http_failures_are_bounded(tmp_path: Path, status, category, caplog):
    private = b"audio-that-must-not-leak"
    provider_body = "provider-payload-that-must-not-leak"
    result = await _transcribe(
        tmp_path, lambda request: httpx.Response(status, text=provider_body), audio=private)
    assert result.outcome == "failed"
    assert result.failure_category == category
    assert result.text == ""
    diagnostics = caplog.text + repr(result)
    assert private.decode() not in diagnostics
    assert base64.b64encode(private).decode() not in diagnostics
    assert provider_body not in diagnostics
    assert "secret-key" not in diagnostics


async def test_master_timeout_is_bounded(tmp_path: Path):
    def handler(request: httpx.Request):
        raise httpx.ReadTimeout("private timeout details", request=request)

    result = await _transcribe(tmp_path, handler)
    assert result.outcome == "failed"
    assert result.failure_category == "timeout"


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"text": ""}),
    httpx.Response(200, json={"other": "value"}),
    httpx.Response(200, text="not json"),
])
async def test_master_malformed_success_is_bounded(tmp_path: Path, response):
    result = await _transcribe(tmp_path, lambda request: response)
    assert result.outcome == "failed"
    assert result.failure_category == "malformed_response"
    assert result.text == ""
