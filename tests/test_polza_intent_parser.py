import asyncio
import json
from datetime import datetime

import httpx
import pytest
import logging

from bot.services.nl_intent import (
    IntentContext, IntentKind, IntentParserInvalidOutput, IntentParserTimeout, IntentParserUnavailable,
)
from bot.services.polza_intent_parser import POLZA_CHAT_COMPLETIONS_URL, SYSTEM_PROMPT, PolzaIntentParser
from bot.services.nl_intent_decoder import (
    INTENT_JSON_SCHEMA, decode_intent, decode_provider_envelope, normalize_provider_envelope,
)


def run(coro):
    return asyncio.run(coro)


def context():
    return IntentContext("actor", datetime(2026, 8, 9, 12, 0), "Europe/Moscow")


def test_intent_kind_keeps_string_serialization_on_python_310():
    intent = IntentKind.ADD_MOVIE_OR_TV

    assert intent.value == "add_movie_or_tv"
    assert str(intent) == "add_movie_or_tv"
    assert json.dumps(intent) == '"add_movie_or_tv"'


def response_content(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def provider_envelope(intent, arguments, **irrelevant):
    fields = {name: None for name in INTENT_JSON_SCHEMA["schema"]["properties"]["arguments"]["properties"]}
    fields.update(arguments)
    fields.update(irrelevant)
    return {"intent": intent, "arguments": fields}


def test_polza_request_contract_uses_configured_model():
    seen = {}
    model = "deepseek/deepseek-v4-flash-0731"

    def transport(request):
        seen["request"] = request
        return response_content(json.dumps(provider_envelope("add_movie_or_tv", {"query": "Дюна"})))

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    result = run(PolzaIntentParser(api_key="secret", model=model, client=client).parse("private text", context()))
    run(client.aclose())
    request = seen["request"]
    payload = json.loads(request.content)
    assert str(request.url) == POLZA_CHAT_COMPLETIONS_URL
    assert request.headers["Authorization"] == "Bearer secret"
    assert payload["model"] == model
    assert payload["stream"] is False
    assert payload["temperature"] == 0
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in payload and "tool_choice" not in payload
    assert "reasoning" not in payload and "reasoning_effort" not in payload
    assert result.intent is IntentKind.ADD_MOVIE_OR_TV
    assert result.arguments == {"query": "Дюна"}


CLASSIFICATION_CASES = [
    ("Добавь стоматолог в календарь 17.08", "add_personal_calendar_event"),
    ("Добавь стоматолога в календарь 17.08", "add_personal_calendar_event"),
    ("Добавь стоматолога в мой календарь 17.08", "add_personal_calendar_event"),
    ("Добавь в календарь стоматолога 17 августа", "add_personal_calendar_event"),
    ("Запланируй стоматолога на завтра", "add_personal_calendar_event"),
    ("Запиши стоматолога в календарь", "add_personal_calendar_event"),
    ("добавь концерт в афишу 20 сентября", "add_afisha_event"),
    ("запиши театр в афишу на субботу", "add_afisha_event"),
    ("добавь кофемашину в покупки", "add_purchase"),
    ("хочу добавить пылесос в покупки за 40000", "add_purchase"),
    ("добавь Во все тяжкие в фильмы", "add_movie_or_tv"),
    ("добавь сериал Офис", "add_movie_or_tv"),
    ("прикольно", "no_action"),
    ("отправь письмо директору", "unsupported"),
    ("добавь Саше в календарь встречу", "unsupported"),
]


def canonical_arguments(intent, phrase):
    if intent == "add_personal_calendar_event":
        date = next((value for value in ("17.08", "17 августа", "завтра") if value in phrase), None)
        return {"title": "стоматолог", "date_expression": date, "time_expression": None,
                "end_time_expression": None, "comment": None, "owner": "current_user"}
    if intent == "add_afisha_event":
        date = "20 сентября" if "20 сентября" in phrase else "на субботу"
        return {"title": "концерт" if "концерт" in phrase else "театр", "place": None,
                "date_expression": date, "time_expression": None, "end_date_expression": None,
                "end_time_expression": None, "link": None}
    if intent == "add_purchase":
        return {"title": "пылесос" if "пылесос" in phrase else "кофемашина",
                "price": 40000 if "40000" in phrase else None, "priority": None,
                "link": None, "comment": None, "buyer": None}
    if intent == "add_movie_or_tv":
        return {"query": "Офис" if "Офис" in phrase else "Во все тяжкие"}
    if intent == "no_action":
        return {}
    return {"category": "other_user_calendar" if "Саше" in phrase else "unsupported_domain"}


@pytest.mark.parametrize(("phrase", "expected_intent"), CLASSIFICATION_CASES)
def test_natural_russian_classification_contract_with_mocked_polza(phrase, expected_intent):
    """Exercise the strict provider boundary without making a live Polza request."""
    arguments = canonical_arguments(expected_intent, phrase)
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: response_content(json.dumps(provider_envelope(expected_intent, arguments)))
    ))
    result = run(PolzaIntentParser(api_key="secret", model="configured/model", client=client).parse(phrase, context()))
    run(client.aclose())

    assert result.intent.value == expected_intent
    assert result.arguments == arguments


def test_prompt_prioritizes_supported_partial_commands_and_preserves_dates():
    assert "Отсутствие даты, времени" in SYSTEM_PROMPT
    assert "НЕ означает unsupported" in SYSTEM_PROMPT
    assert "owner=current_user" in SYSTEM_PROMPT
    assert "17.08" in SYSTEM_PROMPT and "не преобразуй его в ISO" in SYSTEM_PROMPT
    assert "no_action используй только для текста без команды" in SYSTEM_PROMPT


def test_calendar_add_without_date_or_time_keeps_null_slots_for_clarification():
    arguments = canonical_arguments("add_personal_calendar_event", "Запиши стоматолога в календарь")
    result = decode_intent({
        "intent": "add_personal_calendar_event", "arguments": arguments,
    })

    assert result.intent is IntentKind.ADD_PERSONAL_CALENDAR_EVENT
    assert result.arguments["date_expression"] is None
    assert result.arguments["time_expression"] is None


PURCHASE_PHRASES = [
    ("добавь в покупки кофемашину за 35000, высокий приоритет", 35000, "high"),
    ("добавь кофемашину в покупки за 35 тысяч", 35000, None),
    ("хочу купить кофемашину за 35000", 35000, None),
    ("добавь в покупки кофемашину", None, None),
    ("запиши в покупки кофемашину, приоритет высокий", None, "high"),
]


@pytest.mark.parametrize(("phrase", "price", "priority"), PURCHASE_PHRASES)
def test_purchase_natural_phrasings_decode_strict_mocked_provider_output(phrase, price, priority):
    arguments = {
        "title": "кофемашина", "price": price, "priority": priority,
        "link": None, "comment": None, "buyer": None,
    }
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: response_content(json.dumps(provider_envelope("add_purchase", arguments)))
    ))
    result = run(PolzaIntentParser(api_key="secret", model="configured/model", client=client).parse(phrase, context()))
    run(client.aclose())
    assert result.intent is IntentKind.ADD_PURCHASE
    assert result.arguments == arguments


def test_production_price_field_no_longer_causes_unexpected_fields():
    """The production-shaped price field used to differ from decoder's price_text."""
    result = decode_intent({
        "intent": "add_purchase",
        "arguments": {"title": "кофемашина", "price": 35000, "priority": "high",
                      "link": None, "comment": None, "buyer": None},
    })
    assert result.arguments["price"] == 35000


@pytest.mark.parametrize(("intent", "arguments"), [
    ("add_movie_or_tv", {"query": "Дюна"}),
    ("add_purchase", {"title": "Кофемашина", "price": None, "priority": None,
                      "link": None, "comment": None, "buyer": None}),
    ("add_personal_calendar_event", {"title": "Врач", "date_expression": "завтра",
                                     "time_expression": "18:00", "end_time_expression": None,
                                     "comment": None, "owner": "current_user"}),
    ("add_afisha_event", {"title": "Концерт", "place": None, "date_expression": "завтра",
                          "time_expression": "19:00", "end_date_expression": None,
                          "end_time_expression": None, "link": None}),
    ("no_action", {}),
    ("unsupported", {"category": "conversation"}),
])
def test_every_supported_intent_has_a_valid_canonical_envelope(intent, arguments):
    result = decode_intent({"intent": intent, "arguments": arguments})
    assert result.intent.value == intent
    assert result.arguments == arguments


@pytest.mark.parametrize("payload", [
    {"intent": "add_purchase", "arguments": {"title": "X", "price": 1, "priority": None,
                                                "link": None, "comment": None, "buyer": None, "danger": True}},
    {"intent": "add_purchase", "arguments": {"title": "X"}},
    {"intent": "add_purchase", "arguments": {"title": "X", "price": "35000", "priority": None,
                                                "link": None, "comment": None, "buyer": None}},
])
def test_purchase_unknown_missing_and_wrong_typed_fields_remain_rejected(payload):
    with pytest.raises(IntentParserInvalidOutput) as error:
        decode_intent(payload)
    if "danger" in payload["arguments"]:
        assert str(error.value) == "unexpected_fields"


def test_schema_discriminates_every_intent_and_matches_decoder_contract():
    schema = INTENT_JSON_SCHEMA["schema"]
    arguments = schema["properties"]["arguments"]
    assert set(schema["properties"]["intent"]["enum"]) == {kind.value for kind in IntentKind}
    assert len(IntentKind) == 18
    assert arguments["additionalProperties"] is False
    assert set(arguments["required"]) == set(arguments["properties"])
    assert "price_text" not in json.dumps(INTENT_JSON_SCHEMA)


def test_gpt4o_provider_schema_has_supported_root_and_no_discriminator_oneof_or_const():
    """Regression for Polza/GPT-4o's rejection of the former root oneOf schema."""
    schema = INTENT_JSON_SCHEMA["schema"]
    serialized = json.dumps(schema, sort_keys=True)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert '"oneOf"' not in serialized
    assert '"const"' not in serialized
    arguments = schema["properties"]["arguments"]
    assert arguments["type"] == "object"
    assert arguments["additionalProperties"] is False
    assert set(arguments["required"]) == set(arguments["properties"])
    assert '"anyOf"' not in serialized


def test_exact_production_response_format_matches_golden_file():
    actual = json.dumps(
        {"type": "json_schema", "json_schema": INTENT_JSON_SCHEMA},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    with open("tests/fixtures/polza_response_format.json", encoding="utf-8") as fixture:
        assert actual == fixture.read().rstrip("\n")


def test_decode_failure_logs_only_safe_response_shape(caplog):
    secret_value = "sensitive user comment"
    content = json.dumps(provider_envelope("add_purchase", {
        "title": secret_value, "price": 35000, "priority": "urgent", "link": None,
        "comment": secret_value, "buyer": None,
    }))
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response_content(content)))
    with caplog.at_level(logging.WARNING), pytest.raises(IntentParserInvalidOutput):
        run(PolzaIntentParser(api_key="super-secret-key", model="configured/model", client=client).parse("private", context()))
    run(client.aclose())
    log = caplog.text
    assert "reason=invalid_provider_priority" in log and "intent=add_purchase" in log
    assert "argument_keys=['buyer', 'category', 'comment'" in log
    assert secret_value not in log and "super-secret-key" not in log and "private" not in log


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_polza_http_failures_are_mapped(status):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status)))
    with pytest.raises(IntentParserUnavailable):
        run(PolzaIntentParser(api_key="secret", model="configured/model", client=client).parse("text", context()))
    run(client.aclose())


def test_http_400_logs_only_safe_structured_diagnostics(caplog):
    user_text = "delete dentist appointment private title"
    secret = "polza-secret-key"
    response = httpx.Response(400, json={"error": {
        "type": "invalid_request_error", "code": "invalid_json_schema",
        "message": "Invalid response_format json_schema: root oneOf is unsupported; " + user_text,
    }})
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
    with caplog.at_level(logging.WARNING), pytest.raises(IntentParserUnavailable):
        run(PolzaIntentParser(api_key=secret, model="openai/gpt-4o-mini", client=client).parse(user_text, context()))
    run(client.aclose())
    assert "provider_type=invalid_request_error" in caplog.text
    assert "provider_code=invalid_json_schema" in caplog.text
    assert "reason=invalid_structured_output_schema" in caplog.text
    assert user_text not in caplog.text and secret not in caplog.text


def test_http_error_does_not_log_unsafe_type_code_or_arbitrary_body(caplog):
    private = "private title and https://secret.invalid/link"
    response = httpx.Response(400, json={"error": {
        "type": private, "code": "bad code with spaces", "message": private,
    }})
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
    with caplog.at_level(logging.WARNING), pytest.raises(IntentParserUnavailable):
        run(PolzaIntentParser(api_key="key", model="model", client=client).parse(private, context()))
    run(client.aclose())
    assert "provider_type=unknown provider_code=unknown reason=provider_rejected_request" in caplog.text
    assert private not in caplog.text


def test_polza_timeout_is_mapped():
    def transport(request):
        raise httpx.ReadTimeout("slow", request=request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    with pytest.raises(IntentParserTimeout):
        run(PolzaIntentParser(api_key="secret", model="configured/model", client=client).parse("text", context()))
    run(client.aclose())


def test_timeout_and_provider_error_logs_exclude_input_and_credentials(caplog):
    user_text, key = "private dentist title", "polza-super-secret"
    timeout_client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request))))
    with caplog.at_level(logging.INFO), pytest.raises(IntentParserTimeout):
        run(PolzaIntentParser(api_key=key, model="configured/model", client=timeout_client).parse(user_text, context()))
    run(timeout_client.aclose())
    assert "NL parse timeout" in caplog.text
    assert user_text not in caplog.text and key not in caplog.text and "Authorization" not in caplog.text


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


CANONICAL_BY_INTENT = {
    "add_movie_or_tv": {"query": "Дюна"},
    "add_purchase": {"title": "Чайник", "price": None, "priority": None, "link": None, "comment": None, "buyer": None},
    "add_personal_calendar_event": {"title": "Врач", "date_expression": None, "time_expression": None, "end_time_expression": None, "comment": None, "owner": "current_user"},
    "add_afisha_event": {"title": "Концерт", "place": None, "date_expression": None, "time_expression": None, "end_date_expression": None, "end_time_expression": None, "link": None},
    "update_purchase": {"target": "Чайник", "title": None, "price": None, "priority": None, "link": None, "comment": None, "buyer": None, "status": None},
    "delete_purchase": {"target": "Чайник"},
    "update_film": {"target": "Дюна", "status": None, "comment": None},
    "delete_film": {"target": "Дюна"},
    "update_calendar_event": {"target": "Врач", "title": None, "date_expression": None, "time_expression": None},
    "delete_calendar_event": {"target": "Врач"},
    "update_afisha_event": {"target": "Концерт", "title": None, "date_expression": None, "time_expression": None},
    "delete_afisha_event": {"target": "Концерт"},
    "query_purchases": {"status": "planned", "priority": "any", "buyer": "any", "operation": "list"},
    "query_films": {"status": "want", "media_type": "any", "genre": None, "operation": "list"},
    "query_calendar": {"date_from": None, "date_to": None, "target": None, "operation": "list"},
    "query_afisha": {"date_from": None, "date_to": None, "target": None, "operation": "list"},
    "no_action": {},
    "unsupported": {"category": "conversation"},
}


@pytest.mark.parametrize(("intent", "arguments"), CANONICAL_BY_INTENT.items())
def test_all_18_provider_intents_normalize_to_exact_canonical_shape(intent, arguments):
    normalized = normalize_provider_envelope(provider_envelope(intent, arguments))
    assert normalized == {"intent": intent, "arguments": arguments}
    assert decode_provider_envelope(json.dumps(provider_envelope(intent, arguments))).intent.value == intent


def test_production_afisha_calendar_shape_is_not_accepted_for_afisha():
    payload = provider_envelope("add_afisha_event", CANONICAL_BY_INTENT["add_afisha_event"], owner="current_user")
    with pytest.raises(IntentParserInvalidOutput, match="irrelevant_non_null_field"):
        decode_provider_envelope(json.dumps(payload))


def test_delete_calendar_receives_exact_canonical_arguments():
    payload = provider_envelope("delete_calendar_event", CANONICAL_BY_INTENT["delete_calendar_event"])
    assert normalize_provider_envelope(payload)["arguments"] == {"target": "Врач"}


def test_irrelevant_nulls_are_stripped_but_relevant_nulls_are_preserved():
    normalized = normalize_provider_envelope(provider_envelope(
        "add_personal_calendar_event", CANONICAL_BY_INTENT["add_personal_calendar_event"], price=None,
    ))
    assert "price" not in normalized["arguments"]
    assert normalized["arguments"]["comment"] is None


@pytest.mark.parametrize("mutation", ["missing", "unknown", "wrong_type"])
def test_provider_contract_rejects_missing_unknown_and_wrong_typed_fields(mutation):
    payload = provider_envelope("add_purchase", CANONICAL_BY_INTENT["add_purchase"])
    if mutation == "missing":
        payload["arguments"].pop("place")
    elif mutation == "unknown":
        payload["arguments"]["danger"] = None
    else:
        payload["arguments"]["price"] = "35000"
    with pytest.raises(IntentParserInvalidOutput):
        normalize_provider_envelope(payload)


def test_canonical_decoder_remains_authoritative_and_rejects_direct_mismatch():
    with pytest.raises(IntentParserInvalidOutput, match="unexpected_fields"):
        decode_intent({"intent": "delete_calendar_event", "arguments": {
            "title": "Врач", "date_expression": None, "time_expression": None,
            "end_time_expression": None, "comment": None, "owner": "current_user",
        }})
