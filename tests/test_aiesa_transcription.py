import hashlib
import asyncio
import hmac
from pathlib import Path

import httpx
import pytest

from bot.services.aiesa_transcription import (AiesaError, AiesaTranscriptionService,
                                               auth_headers, is_transient_status)


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_auth_signature_contract():
    headers = auth_headers("public", "secret", "1700000000")
    expected = hmac.new(b"secret", b"public\n1700000000", hashlib.sha256).hexdigest()
    assert headers == {"X-Public-Key": "public", "X-Timestamp": "1700000000", "X-Signature": expected}


async def test_create_parses_201(tmp_path: Path):
    media = tmp_path / "a.ogg"
    media.write_bytes(b"audio")
    client = client_for(lambda request: httpx.Response(201, json={"transcription_id": "uuid"}))
    try:
        assert await AiesaTranscriptionService("p", "s", client=client).create(media, "a.ogg", "audio/ogg") == "uuid"
    finally:
        await client.aclose()


@pytest.mark.parametrize("provider_status", ["processing", "completed", "failed"])
async def test_nested_status_parsing(provider_status):
    payload = {"data": {"id": "uuid", "status": provider_status, "progress": 100,
                        "duration_seconds": 203.93, "minutes_billed": 4,
                        "results": {"json": "https://results.example/result.json"}}}
    client = client_for(lambda request: httpx.Response(200, json=payload))
    try:
        result = await AiesaTranscriptionService("p", "s", client=client).status("uuid")
        assert result.status == provider_status
        assert result.duration_seconds == 203.93
    finally:
        await client.aclose()


async def test_result_json_verified_shape_and_missing_speaker():
    payload = {"transcription": {"duration": 3.2, "segments": [
        {"id": 0, "speaker": "SPEAKER_01", "start": 0, "end": 1, "start_time": "00:00:00", "end_time": "00:00:01", "text": "Привет"},
        {"id": 1, "start": 1, "end": 2, "text": "мир"},
    ]}}
    client = client_for(lambda request: httpx.Response(200, json=payload))
    try:
        result = await AiesaTranscriptionService("p", "s", client=client).result("https://results.example/result.json")
        assert len(result.segments) == 2
        assert result.speaker_count == 2
        assert result.segments[1].speaker == "UNKNOWN"
    finally:
        await client.aclose()


async def test_malformed_and_network_timeout_are_bounded():
    malformed = client_for(lambda request: httpx.Response(200, json={"wrong": {}}))
    with pytest.raises(AiesaError, match="malformed_status_response"):
        await AiesaTranscriptionService("p", "s", client=malformed).status("uuid")
    await malformed.aclose()

    def timeout(request):
        raise httpx.ReadTimeout("timeout")
    timed = client_for(timeout)
    with pytest.raises(AiesaError) as caught:
        await AiesaTranscriptionService("p", "s", client=timed).status("uuid")
    assert caught.value.transient is True
    await timed.aclose()


def test_retry_classification():
    assert is_transient_status(429) and is_transient_status(500) and is_transient_status(503)
    assert not is_transient_status(400) and not is_transient_status(404)
