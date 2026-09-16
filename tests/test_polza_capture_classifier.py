import asyncio
import json

import httpx
import pytest

from bot.services.capture import CaptureContext, CaptureValidationError, normalize_text_capture
from bot.services.polza_capture_classifier import (MAX_CAPTURE_PROVIDER_RESPONSE_BYTES,
                                                   PolzaCaptureClassifier)
from bot.services.nl_dates import zoned_now


def envelope(value):
    return {"choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}}]}


def valid(destination, reason, candidate, *, certainty="clear", alternatives=None):
    return {"contract_version": 2, "destination": destination, "certainty": certainty,
            "alternatives": alternatives or [], "reason_code": reason, "candidate": candidate}


def run_classifier(response, text="source"):
    async def handler(request):
        return response(request) if callable(response) else response
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    classifier = PolzaCaptureClassifier(api_key="secret", model="model", client=client)
    try:
        return asyncio.run(classifier.classify(normalize_text_capture(text), CaptureContext(
            zoned_now("Europe/Moscow"), "Europe/Moscow")))
    finally:
        asyncio.run(client.aclose())


@pytest.mark.parametrize(("destination", "reason", "candidate", "text", "certainty", "alternatives"), [
    ("films", "film_watch_intent", {"kind": "films", "query": "Паразитов"}, "source", "clear", []),
    ("purchases", "purchase_intent", {"kind": "purchases", "title": "Штатив"}, "source", "clear", []),
    ("wishlist", "item_desire_ambiguous", {"kind": "wishlist", "title": "Наушники"}, "source", "ambiguous", ["purchases"]),
    ("leisure", "leisure_idea", {"kind": "leisure", "title": "Квест"}, "source", "clear", []),
    ("places", "place_intent", {"kind": "places", "name": "Бар X"}, "source", "clear", []),
    ("afisha", "scheduled_event", {"kind": "afisha", "title": "Концерт", "date_expression": "25 сентября"}, "source", "clear", []),
    ("notes", "personal_thought", {"kind": "notes", "text": "source"}, "source", "clear", []),
])
def test_provider_accepts_all_destination_responses(destination, reason, candidate, text,
                                                    certainty, alternatives):
    raw = valid(destination, reason, candidate, certainty=certainty, alternatives=alternatives)
    assert run_classifier(httpx.Response(200, json=envelope(raw)), text)["destination"] == destination


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}),
    httpx.Response(200, json=envelope({"wrong": "schema"})),
    httpx.Response(503, json={}),
    httpx.Response(200, content=b"x" * (MAX_CAPTURE_PROVIDER_RESPONSE_BYTES + 1)),
])
def test_provider_fails_closed_for_malformed_schema_http_and_oversized_response(response):
    with pytest.raises(CaptureValidationError):
        run_classifier(response)


def test_provider_timeout_fails_closed():
    def timeout(request):
        raise httpx.ReadTimeout("timeout", request=request)
    with pytest.raises(CaptureValidationError, match="provider_timeout"):
        run_classifier(timeout)


def test_provider_logs_only_structural_metadata(caplog):
    private = "совершенно секретная мысль"
    raw = valid("notes", "personal_thought", {"kind": "notes", "text": private})
    with caplog.at_level("INFO"):
        run_classifier(httpx.Response(200, json=envelope(raw)), private)
    assert private not in caplog.text
    assert "destination=notes" in caplog.text
    assert "certainty=clear" in caplog.text
    assert "reason_code=personal_thought" in caplog.text
