import asyncio
import json
from datetime import datetime

import httpx
import pytest

from bot.services.nl_intent import (
    IntentContext, IntentKind, IntentParserInvalidOutput, IntentParserTimeout, IntentParserUnavailable,
)
from bot.services.polza_intent_parser import POLZA_CHAT_COMPLETIONS_URL, PolzaIntentParser


def run(coro):
    return asyncio.run(coro)


def context():
    return IntentContext("actor", datetime(2026, 8, 9, 12, 0), "Europe/Moscow")


def response_content(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_polza_request_contract_and_model_is_unchanged():
    seen = {}
    model = "deepseek/deepseek-v4-flash-0731"

    def transport(request):
        seen["request"] = request
        return response_content(json.dumps({"intent": "add_movie_or_tv", "arguments": {"query": "Дюна"}}))

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    result = run(PolzaIntentParser(api_key="secret", model=model, client=client).parse("private text", context()))
    run(client.aclose())
    request = seen["request"]
    payload = json.loads(request.content)
    assert str(request.url) == POLZA_CHAT_COMPLETIONS_URL
    assert request.headers["Authorization"] == "Bearer secret"
    assert payload["model"] == model
    assert payload["stream"] is False
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in payload and "tool_choice" not in payload
    assert "reasoning" not in payload and "reasoning_effort" not in payload
    assert result.intent is IntentKind.ADD_MOVIE_OR_TV
    assert result.arguments == {"query": "Дюна"}


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_polza_http_failures_are_mapped(status):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status)))
    with pytest.raises(IntentParserUnavailable):
        run(PolzaIntentParser(api_key="secret", model="configured/model", client=client).parse("text", context()))
    run(client.aclose())


def test_polza_timeout_is_mapped():
    def transport(request):
        raise httpx.ReadTimeout("slow", request=request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    with pytest.raises(IntentParserTimeout):
        run(PolzaIntentParser(api_key="secret", model="configured/model", client=client).parse("text", context()))
    run(client.aclose())


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"choices": []}),
    response_content("not json"),
    response_content(json.dumps({"intent": "delete_everything", "arguments": {}})),
    response_content(json.dumps({"intent": "add_purchase", "arguments": {"title": "X"}})),
])
def test_polza_malformed_or_invalid_output_is_rejected(response):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(IntentParserInvalidOutput):
        run(PolzaIntentParser(api_key="secret", model="configured/model", client=client).parse("text", context()))
    run(client.aclose())


def test_polza_requires_key_and_environment_supplied_model():
    with pytest.raises(ValueError):
        PolzaIntentParser(api_key="", model="configured/model")
    with pytest.raises(ValueError):
        PolzaIntentParser(api_key="secret", model="")
