import asyncio
import json

import httpx
import pytest

from bot.services.capture import (CAPTURE_CLASSIFICATION_JSON_SCHEMA, CaptureContext,
                                  CaptureValidationError, normalize_text_capture)
from bot.services.polza_capture_classifier import (MAX_CAPTURE_PROVIDER_RESPONSE_BYTES,
                                                   PolzaCaptureClassifier)
from bot.services.nl_dates import zoned_now


def envelope(value):
    return {"choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}}]}


def valid(destination, reason, candidate, *, certainty="clear", alternatives=None):
    return {"contract_version": 2, "destination": destination, "certainty": certainty,
            "alternatives": alternatives or [], "reason_code": reason, "candidate": candidate}


def provider_wire(value):
    fields = CAPTURE_CLASSIFICATION_JSON_SCHEMA["schema"]["properties"]["candidate"]["properties"]
    return {**value, "candidate": {name: value["candidate"].get(name) for name in fields}}


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
    assert run_classifier(httpx.Response(200, json=envelope(provider_wire(raw))), text)["destination"] == destination


def test_outgoing_request_uses_polza_compatible_strict_schema():
    seen = {}
    raw = provider_wire(valid("films", "film_watch_intent", {"kind": "films", "query": "Паразитов"}))

    def response(request):
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json=envelope(raw))

    run_classifier(response)
    response_format = seen["payload"]["response_format"]
    assert response_format == {"type": "json_schema", "json_schema": CAPTURE_CLASSIFICATION_JSON_SCHEMA}
    assert response_format["json_schema"]["strict"] is True

    schema = response_format["json_schema"]["schema"]
    candidate = schema["properties"]["candidate"]
    assert set(schema["required"]) == set(schema["properties"])
    assert set(candidate["required"]) == set(candidate["properties"])
    assert candidate["additionalProperties"] is False
    assert candidate["properties"]["kind"]["enum"] == [
        "afisha", "films", "leisure", "notes", "places", "purchases", "wishlist"]
    for name, field_schema in candidate["properties"].items():
        if name != "kind":
            assert "null" in field_schema["type"], name
    serialized = json.dumps(schema, sort_keys=True)
    assert '"oneOf"' not in serialized
    assert '"anyOf"' not in serialized
    assert '"const"' not in serialized


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
    raw = provider_wire(valid("notes", "personal_thought", {"kind": "notes", "text": private}))
    with caplog.at_level("INFO"):
        run_classifier(httpx.Response(200, json=envelope(raw)), private)
    assert private not in caplog.text
    assert "destination=notes" in caplog.text
    assert "certainty=clear" in caplog.text
    assert "reason_code=personal_thought" in caplog.text
