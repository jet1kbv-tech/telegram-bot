from pathlib import Path

import httpx

from bot.services.polza_master_transcription import (POLZA_AUDIO_TRANSCRIPTIONS_URL,
                                                     PolzaMasterTranscriptionService)


async def test_master_transcription_multipart_contract(tmp_path: Path):
    path = tmp_path / "private.ogg"
    path.write_bytes(b"audio")
    def handler(request):
        assert str(request.url) == POLZA_AUDIO_TRANSCRIPTIONS_URL
        assert request.headers["authorization"] == "Bearer key"
        body = request.read()
        assert b"openai/gpt-4o-mini-transcribe" in body and b'ru' in body and b'json' in body
        return httpx.Response(200, json={"text": "Привет, мир."})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await PolzaMasterTranscriptionService("key", "openai/gpt-4o-mini-transcribe",
                                                       client=client).transcribe(path, "private.ogg", "audio/ogg")
    finally:
        await client.aclose()
    assert result.outcome == "success" and result.text == "Привет, мир."


async def test_master_failure_is_safe_and_content_free(tmp_path: Path):
    path = tmp_path / "secret.ogg"
    path.write_bytes(b"private audio")
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(503, json={"private": "payload"})))
    try:
        result = await PolzaMasterTranscriptionService("credential", "model", client=client).transcribe(
            path, "secret.ogg", "audio/ogg")
    finally:
        await client.aclose()
    assert result.outcome == "failed" and result.failure_category == "provider_unavailable"
    assert result.text == ""
