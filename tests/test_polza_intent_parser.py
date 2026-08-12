import asyncio
import json
from datetime import datetime

import httpx
import pytest
import logging

from bot.services.nl_intent import (
    IntentContext, IntentKind, IntentParserInvalidOutput, IntentParserTimeout, IntentParserUnavailable,
)
from bot.services.polza_intent_parser import (
    POLZA_CHAT_COMPLETIONS_URL, SYSTEM_PROMPT, PolzaIntentParser, provider_buyer_diagnostic,
    provider_priority_diagnostic,
)
from bot.services.nl_intent_decoder import (
    INTENT_JSON_SCHEMA, decode_intent, decode_provider_envelope, normalize_provider_envelope,
    provider_rejection_diagnostics,
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


def provider_envelope(intent, arguments, **extra):
    values = {**arguments, **extra}
    return {"intent": intent, "arguments": [
        {"name": name, "value": str(value)}
        for name, value in values.items() if value is not None
    ]}


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
    assert "calendar/afisha operation строго list/count/next" in SYSTEM_PROMPT
    assert "обычный показ списка = list" in SYSTEM_PROMPT
    assert "Цену покупки передавай только цифрами без валюты" in SYSTEM_PROMPT


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
    assert set(schema["properties"]["intent"]["enum"]) == {kind.value for kind in IntentKind}
    assert len(IntentKind) == 22
    assert set(schema["properties"]) == {"intent", "arguments"}
    assert set(schema["required"]) == set(schema["properties"])
    assert "price_text" not in json.dumps(INTENT_JSON_SCHEMA)


def test_decodes_attachment_retrieval_contract():
    parsed = decode_provider_envelope({"intent": "query_event_attachments", "arguments": [
        {"name": "semantic_type", "value": "transport_ticket"},
        {"name": "origin", "value": "Москва"},
        {"name": "destination", "value": "Воронеж"},
        {"name": "direction", "value": "return"},
        {"name": "return_all", "value": "true"},
    ]})
    assert parsed.intent is IntentKind.QUERY_EVENT_ATTACHMENTS
    assert parsed.arguments == {"target": None, "semantic_type": "transport_ticket",
        "transport_type": None, "origin": "Москва", "destination": "Воронеж",
        "date": None, "person": None, "direction": "return", "return_all": True}


def test_gpt4o_provider_schema_has_supported_root_and_no_discriminator_oneof_or_const():
    """Regression for Polza/GPT-4o's rejection of the former root oneOf schema."""
    schema = INTENT_JSON_SCHEMA["schema"]
    serialized = json.dumps(schema, sort_keys=True)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert '"oneOf"' not in serialized
    assert '"const"' not in serialized
    assert schema["properties"]["arguments"]["type"] == "array"
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
    assert "reason=invalid_priority" in log and "intent=add_purchase" in log
    assert "provider_priority=unknown" in log
    assert "conflicting_fields=[]" in log
    assert secret_value not in log and "super-secret-key" not in log and "private" not in log


@pytest.mark.parametrize(("priority", "diagnostic"), [
    ("any", "any"), ("urgent", "unknown"), ("high", "high"),
])
def test_priority_rejection_logs_safe_provider_category(priority, diagnostic, caplog):
    if priority == "urgent":
        payload = provider_envelope("add_purchase", {
            **CANONICAL_BY_INTENT["add_purchase"], "priority": priority,
        })
    else:
        payload = provider_envelope("query_purchases", {
            **CANONICAL_BY_INTENT["query_purchases"], "status": "invalid", "priority": priority,
        })
    content = json.dumps(payload)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response_content(content)))
    with caplog.at_level(logging.WARNING), pytest.raises(IntentParserInvalidOutput):
        run(PolzaIntentParser(api_key="secret", model="configured/model", client=client).parse("private", context()))
    run(client.aclose())
    assert f"provider_priority={diagnostic}" in caplog.text


def test_unsafe_priority_rejection_logs_unknown_and_never_raw_content(caplog):
    unsafe = "urgent priority https://private.invalid/token?credential=hunter2"
    content = json.dumps(provider_envelope("add_purchase", {
        **CANONICAL_BY_INTENT["add_purchase"], "title": "private purchase title", "priority": unsafe,
    }))
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response_content(content)))
    with caplog.at_level(logging.WARNING), pytest.raises(IntentParserInvalidOutput):
        run(PolzaIntentParser(api_key="super-secret-key", model="configured/model", client=client).parse("private command", context()))
    run(client.aclose())
    assert "provider_priority=unknown" in caplog.text
    assert unsafe not in caplog.text
    assert "private purchase title" not in caplog.text
    assert "super-secret-key" not in caplog.text and "private command" not in caplog.text


@pytest.mark.parametrize("content", [
    json.dumps(provider_envelope("query_purchases", {
        "status": "planned", "buyer": "any", "operation": "list",
    })),
    json.dumps({"intent": "add_purchase", "arguments": [
        {"name": "title", "value": "Чайник"}, {"name": "priority", "value": None},
    ]}),
])
def test_missing_query_and_null_mutation_priorities_no_longer_log_rejections(content, caplog):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response_content(content)))
    with caplog.at_level(logging.WARNING):
        run(PolzaIntentParser(api_key="secret", model="configured/model", client=client).parse("private", context()))
    run(client.aclose())
    assert "normalization_rejection" not in caplog.text


def test_priority_diagnostic_categories_are_deterministic():
    missing = json.dumps(provider_envelope("query_purchases", {
        "status": "planned", "buyer": "any", "operation": "list",
    }))
    null = json.dumps({"intent": "add_purchase", "arguments": [{"name": "priority", "value": None}]})
    string_null = json.dumps({
        "intent": "add_purchase", "arguments": [{"name": "priority", "value": "null"}],
    })

    assert provider_priority_diagnostic(missing, "invalid_priority") == "missing"
    assert provider_priority_diagnostic(null, "invalid_argument_entry") == "json_null"
    assert provider_priority_diagnostic(string_null, "invalid_priority") == "string_null"


@pytest.mark.parametrize(("buyer", "expected"), [
    (None, "json_null"), ("null", "string_null"),
    ("current_user", "current_user"), ("private buyer value", "unknown"),
])
def test_buyer_diagnostic_categories_are_safe(buyer, expected):
    raw = json.dumps({"intent": "add_purchase", "arguments": [{"name": "buyer", "value": buyer}]})
    assert provider_buyer_diagnostic(raw, "invalid_buyer") == expected


def test_buyer_diagnostic_distinguishes_missing_and_never_returns_arbitrary_text():
    raw = json.dumps({"intent": "add_purchase", "arguments": []})
    assert provider_buyer_diagnostic(raw, "invalid_buyer") == "missing"


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


def test_omitted_provider_values_become_canonical_nulls():
    normalized = normalize_provider_envelope(provider_envelope(
        "add_personal_calendar_event", CANONICAL_BY_INTENT["add_personal_calendar_event"], price=None,
    ))
    assert normalized["arguments"]["comment"] is None


@pytest.mark.parametrize("payload", [
    {"intent": "add_purchase", "arguments": [{"name": "danger", "value": "x"}]},
    {"intent": "add_purchase", "arguments": [{"name": "price", "value": 35000}]},
    {"intent": "add_purchase", "arguments": [{"name": "title", "value": "X", "extra": "x"}]},
])
def test_provider_contract_rejects_unknown_and_malformed_entries(payload):
    with pytest.raises(IntentParserInvalidOutput):
        normalize_provider_envelope(payload)


def test_canonical_decoder_remains_authoritative_and_rejects_direct_mismatch():
    with pytest.raises(IntentParserInvalidOutput, match="unexpected_fields"):
        decode_intent({"intent": "delete_calendar_event", "arguments": {
            "title": "Врач", "date_expression": None, "time_expression": None,
            "end_time_expression": None, "comment": None, "owner": "current_user",
        }})

PRODUCTION_LEAKAGE_REGRESSIONS = {
    "delete_calendar_event": CANONICAL_BY_INTENT["delete_calendar_event"],
    "query_calendar": CANONICAL_BY_INTENT["query_calendar"],
    "update_calendar_event": CANONICAL_BY_INTENT["update_calendar_event"],
    "add_purchase": CANONICAL_BY_INTENT["add_purchase"],
    "add_movie_or_tv": CANONICAL_BY_INTENT["add_movie_or_tv"],
}


@pytest.mark.parametrize(("intent", "arguments"), PRODUCTION_LEAKAGE_REGRESSIONS.items())
def test_all_production_intents_cross_compact_provider_adapter(intent, arguments):
    """Production intents cross the compact provider adapter exactly."""
    assert normalize_provider_envelope(provider_envelope(intent, arguments)) == {
        "intent": intent, "arguments": arguments,
    }


@pytest.mark.parametrize("intent", [
    "add_afisha_event", "add_personal_calendar_event", "query_afisha",
])
def test_production_successes_remain_supported_by_compact_contract(intent):
    arguments = CANONICAL_BY_INTENT[intent]
    assert decode_provider_envelope(json.dumps(provider_envelope(intent, arguments))).arguments == arguments


def test_genuine_cross_domain_price_conflict_is_not_silently_discarded():
    payload = provider_envelope(
        "add_afisha_event", CANONICAL_BY_INTENT["add_afisha_event"], price=35000,
    )
    with pytest.raises(IntentParserInvalidOutput, match="irrelevant_non_null_field"):
        normalize_provider_envelope(payload)


def test_normalization_rejection_logs_conflicting_names_without_values(caplog):
    content = json.dumps(provider_envelope(
        "add_afisha_event", CANONICAL_BY_INTENT["add_afisha_event"], price=35000,
    ))
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response_content(content)))
    with caplog.at_level(logging.WARNING), pytest.raises(IntentParserInvalidOutput):
        run(PolzaIntentParser(api_key="secret", model="openai/gpt-4o-mini", client=client).parse("private", context()))
    run(client.aclose())
    assert "intent=add_afisha_event" in caplog.text
    assert "reason=irrelevant_non_null_field" in caplog.text
    assert "conflicting_fields=['price']" in caplog.text
    assert "35000" not in caplog.text and "private" not in caplog.text

# Production regressions from the third (flat-superset) contract.
def test_production_query_calendar_redundant_current_user_owner_is_safe():
    payload = provider_envelope("query_calendar", CANONICAL_BY_INTENT["query_calendar"], owner="current_user")
    assert decode_provider_envelope(json.dumps(payload)).arguments == CANONICAL_BY_INTENT["query_calendar"]


def test_normal_calendar_this_week_uses_canonical_list_operation():
    payload = provider_envelope("query_calendar", {
        "date_from": "на этой неделе", "date_to": "на этой неделе",
        "target": None, "operation": "list",
    })
    result = decode_provider_envelope(json.dumps(payload))
    assert result.arguments["operation"] == "list"


@pytest.mark.parametrize("operation", ["show", "get", "all", "list_or_next", "показать"])
def test_query_calendar_unrecognized_or_ambiguous_operations_remain_rejected(operation):
    payload = provider_envelope("query_calendar", {
        **CANONICAL_BY_INTENT["query_calendar"], "operation": operation,
    })
    with pytest.raises(IntentParserInvalidOutput, match="invalid_query_arguments"):
        decode_provider_envelope(json.dumps(payload))


@pytest.mark.parametrize("intent", [
    "query_purchases", "query_films", "query_calendar", "query_afisha",
])
def test_query_missing_operation_uses_existing_list_default(intent):
    arguments = {**CANONICAL_BY_INTENT[intent]}
    arguments.pop("operation")

    result = decode_provider_envelope(json.dumps(provider_envelope(intent, arguments)))

    assert result.arguments["operation"] == "list"
    for name, value in arguments.items():
        assert result.arguments[name] == value


@pytest.mark.parametrize("operation", ["list", "count", "next"])
def test_every_canonical_query_calendar_operation_is_accepted(operation):
    payload = provider_envelope("query_calendar", {
        **CANONICAL_BY_INTENT["query_calendar"], "operation": operation,
    })
    assert decode_provider_envelope(json.dumps(payload)).arguments["operation"] == operation


def test_add_purchase_missing_priority_uses_native_no_priority_default():
    arguments = {**CANONICAL_BY_INTENT["add_purchase"]}
    arguments.pop("priority")

    result = decode_provider_envelope(json.dumps(provider_envelope("add_purchase", arguments)))

    assert result.arguments["priority"] is None
    assert result.arguments["title"] == "Чайник"


def test_query_purchases_missing_priority_uses_any_filter_default():
    arguments = {**CANONICAL_BY_INTENT["query_purchases"]}
    arguments.pop("priority")
    result = decode_provider_envelope(json.dumps(provider_envelope("query_purchases", arguments)))
    assert result.arguments["priority"] == "any"


@pytest.mark.parametrize(("missing", "expected"), [
    ("status", "want"),
    ("media_type", "any"),
    ("genre", None),
    ("operation", "list"),
])
def test_query_films_missing_field_uses_existing_ordinary_query_default(missing, expected):
    arguments = {**CANONICAL_BY_INTENT["query_films"]}
    arguments.pop(missing)

    result = decode_provider_envelope(json.dumps(provider_envelope("query_films", arguments)))

    assert result.arguments[missing] == expected


@pytest.mark.parametrize(("name", "value"), [
    ("status", "watched"), ("media_type", "movie"), ("media_type", "tv"),
    ("operation", "count"), ("operation", "random"),
])
def test_query_films_explicit_canonical_values_are_preserved(name, value):
    arguments = {**CANONICAL_BY_INTENT["query_films"], name: value}
    result = decode_provider_envelope(json.dumps(provider_envelope("query_films", arguments)))
    assert result.arguments[name] == value


def test_query_films_explicit_invalid_media_type_remains_rejected():
    arguments = {**CANONICAL_BY_INTENT["query_films"], "media_type": "series"}
    with pytest.raises(IntentParserInvalidOutput, match="invalid_query_arguments"):
        decode_provider_envelope(json.dumps(provider_envelope("query_films", arguments)))


@pytest.mark.parametrize(("missing", "expected"), [
    ("buyer", "any"),
    ("status", "planned"),
])
def test_query_purchases_missing_filter_uses_existing_ordinary_query_default(missing, expected):
    arguments = {**CANONICAL_BY_INTENT["query_purchases"]}
    arguments.pop(missing)

    result = decode_provider_envelope(json.dumps(provider_envelope("query_purchases", arguments)))

    assert result.arguments[missing] == expected


@pytest.mark.parametrize(("name", "value"), [
    ("buyer", "any"), ("buyer", "current_user"),
    ("status", "planned"), ("status", "bought"), ("status", "any"),
])
def test_query_purchase_explicit_canonical_filters_are_preserved(name, value):
    payload = provider_envelope("query_purchases", {
        **CANONICAL_BY_INTENT["query_purchases"], name: value,
    })
    assert decode_provider_envelope(json.dumps(payload)).arguments[name] == value


@pytest.mark.parametrize(("name", "value"), [("buyer", "everybody"), ("status", "open")])
def test_query_purchase_explicit_invalid_filters_remain_rejected(name, value):
    payload = provider_envelope("query_purchases", {
        **CANONICAL_BY_INTENT["query_purchases"], name: value,
    })
    with pytest.raises(IntentParserInvalidOutput, match="invalid_query_arguments"):
        decode_provider_envelope(json.dumps(payload))


@pytest.mark.parametrize(("intent", "buyer", "expected"), [
    ("add_purchase", None, None),
    ("add_purchase", "null", None),
    ("add_purchase", "current_user", "current_user"),
    ("update_purchase", None, None),
    ("update_purchase", "null", None),
    ("update_purchase", "current_user", "current_user"),
    ("update_purchase", "none", "none"),
])
def test_mutating_purchase_explicit_buyer_values_are_narrowly_normalized(intent, buyer, expected):
    arguments = {**CANONICAL_BY_INTENT[intent]}
    arguments.pop("buyer")
    payload = provider_envelope(intent, arguments)
    payload["arguments"].append({"name": "buyer", "value": buyer})

    assert decode_provider_envelope(json.dumps(payload)).arguments["buyer"] == expected


@pytest.mark.parametrize("intent", ["add_purchase", "update_purchase"])
def test_mutating_purchase_omitted_buyer_retains_canonical_none(intent):
    arguments = {**CANONICAL_BY_INTENT[intent]}
    arguments.pop("buyer")
    result = decode_provider_envelope(json.dumps(provider_envelope(intent, arguments)))
    assert result.arguments["buyer"] is None


@pytest.mark.parametrize(("intent", "buyer"), [
    ("add_purchase", "none"), ("add_purchase", "somebody"),
    ("update_purchase", "somebody"),
])
def test_mutating_purchase_invalid_buyer_values_remain_rejected(intent, buyer):
    payload = provider_envelope(intent, {**CANONICAL_BY_INTENT[intent], "buyer": buyer})
    with pytest.raises(IntentParserInvalidOutput, match="invalid_buyer"):
        decode_provider_envelope(json.dumps(payload))


@pytest.mark.parametrize("buyer", [None, "null"])
def test_query_purchase_null_buyer_values_remain_rejected(buyer):
    arguments = {**CANONICAL_BY_INTENT["query_purchases"]}
    arguments.pop("buyer")
    payload = provider_envelope("query_purchases", arguments)
    payload["arguments"].append({"name": "buyer", "value": buyer})
    reason = "invalid_argument_entry" if buyer is None else "invalid_query_arguments"
    with pytest.raises(IntentParserInvalidOutput, match=reason):
        decode_provider_envelope(json.dumps(payload))


def test_invalid_buyer_log_uses_safe_category_without_exposing_value(caplog):
    private = "private buyer https://secret.invalid/credential"
    content = json.dumps(provider_envelope("add_purchase", {
        **CANONICAL_BY_INTENT["add_purchase"], "buyer": private,
    }))
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response_content(content)))
    with caplog.at_level(logging.WARNING), pytest.raises(IntentParserInvalidOutput, match="invalid_buyer"):
        run(PolzaIntentParser(api_key="secret", model="configured/model", client=client).parse("private command", context()))
    run(client.aclose())
    assert "provider_buyer=unknown" in caplog.text
    assert private not in caplog.text and "private command" not in caplog.text


@pytest.mark.parametrize("priority", ["any", "high"])
def test_query_purchase_canonical_priority_is_preserved(priority):
    payload = provider_envelope("query_purchases", {
        **CANONICAL_BY_INTENT["query_purchases"], "priority": priority,
    })
    assert decode_provider_envelope(json.dumps(payload)).arguments["priority"] == priority


@pytest.mark.parametrize(("priority", "expected"), [
    (" высокий ", "high"), ("ВЫСОКАЯ", "high"), ("высокое", "high"),
    ("средний", "medium"), ("средняя", "medium"), ("среднее", "medium"),
    ("низкий", "low"), ("низкая", "low"), ("низкое", "low"),
])
@pytest.mark.parametrize("intent", ["add_purchase", "update_purchase", "query_purchases"])
def test_purchase_priority_exact_russian_aliases_are_intent_scoped(intent, priority, expected):
    payload = provider_envelope(intent, {
        **CANONICAL_BY_INTENT[intent], "priority": priority,
    })
    assert decode_provider_envelope(json.dumps(payload)).arguments["priority"] == expected


@pytest.mark.parametrize("intent", ["add_purchase", "update_purchase"])
def test_mutating_purchase_explicit_null_priority_is_canonical_none(intent):
    payload = provider_envelope(intent, CANONICAL_BY_INTENT[intent])
    payload["arguments"].append({"name": "priority", "value": None})
    assert decode_provider_envelope(json.dumps(payload)).arguments["priority"] is None


def test_production_shaped_add_purchase_explicit_json_null_priority_is_canonical_none():
    payload = {
        "intent": "add_purchase",
        "arguments": [
            {"name": "title", "value": "Кофемолка"},
            {"name": "price", "value": "15000"},
            {"name": "priority", "value": None},
        ],
    }

    assert decode_provider_envelope(json.dumps(payload, ensure_ascii=False)).arguments == {
        "title": "Кофемолка", "price": 15000, "priority": None,
        "link": None, "comment": None, "buyer": None,
    }


def test_production_shaped_add_purchase_string_null_priority_is_canonical_none():
    payload = {
        "intent": "add_purchase",
        "arguments": [
            {"name": "title", "value": "Кофемолка"},
            {"name": "price", "value": "15000"},
            {"name": "priority", "value": "null"},
        ],
    }

    assert decode_provider_envelope(json.dumps(payload, ensure_ascii=False)).arguments == {
        "title": "Кофемолка", "price": 15000, "priority": None,
        "link": None, "comment": None, "buyer": None,
    }


@pytest.mark.parametrize("provider_priority", [None, "null"])
def test_update_purchase_null_priority_is_canonical_none(provider_priority):
    payload = provider_envelope("update_purchase", {
        **CANONICAL_BY_INTENT["update_purchase"], "priority": provider_priority,
    })

    assert decode_provider_envelope(json.dumps(payload)).arguments["priority"] is None


def test_update_purchase_canonical_none_priority_retains_clear_semantics():
    payload = provider_envelope("update_purchase", {
        **CANONICAL_BY_INTENT["update_purchase"], "priority": "none",
    })

    assert decode_provider_envelope(json.dumps(payload)).arguments["priority"] == "none"


def test_query_purchase_explicit_json_null_priority_remains_rejected():
    arguments = {**CANONICAL_BY_INTENT["query_purchases"]}
    arguments.pop("priority")
    payload = provider_envelope("query_purchases", arguments)
    payload["arguments"].append({"name": "priority", "value": None})
    with pytest.raises(IntentParserInvalidOutput, match="invalid_argument_entry"):
        decode_provider_envelope(json.dumps(payload))


@pytest.mark.parametrize("intent", ["add_purchase", "update_purchase", "query_purchases"])
@pytest.mark.parametrize("priority", ["urgent", "срочно", "обычный", "важный"])
def test_purchase_priority_unknown_explicit_values_remain_rejected(intent, priority):
    payload = provider_envelope(intent, {
        **CANONICAL_BY_INTENT[intent], "priority": priority,
    })
    reason = "invalid_query_arguments" if intent == "query_purchases" else "invalid_priority"
    with pytest.raises(IntentParserInvalidOutput, match=reason):
        decode_provider_envelope(json.dumps(payload))


@pytest.mark.parametrize("priority", ["high", "medium", "low"])
def test_add_purchase_explicit_valid_priority_is_preserved(priority):
    payload = provider_envelope("add_purchase", {
        **CANONICAL_BY_INTENT["add_purchase"], "priority": priority,
    })
    assert decode_provider_envelope(json.dumps(payload)).arguments["priority"] == priority


@pytest.mark.parametrize("priority", ["high", "medium", "low"])
def test_update_purchase_explicit_valid_priority_is_preserved(priority):
    payload = provider_envelope("update_purchase", {
        **CANONICAL_BY_INTENT["update_purchase"], "priority": priority,
    })
    assert decode_provider_envelope(json.dumps(payload)).arguments["priority"] == priority


@pytest.mark.parametrize("priority", ["none", "any", "urgent", "no priority", "нет"])
def test_add_purchase_explicit_invalid_priority_remains_rejected(priority):
    payload = provider_envelope("add_purchase", {
        **CANONICAL_BY_INTENT["add_purchase"], "priority": priority,
    })
    with pytest.raises(IntentParserInvalidOutput, match="invalid_priority"):
        decode_provider_envelope(json.dumps(payload))


@pytest.mark.parametrize(("provider_price", "expected"), [
    ("35000", 35_000),
    ("35 000", 35_000),
    ("35\u00a0000", 35_000),
    ("35_000", 35_000),
    ("35000 ₽", 35_000),
    ("35000 руб", 35_000),
    ("35000 рублей", 35_000),
    ("0", 0),
    ("1000000000", 1_000_000_000),
])
def test_provider_purchase_price_accepts_small_explicit_ruble_grammar(provider_price, expected):
    payload = provider_envelope("add_purchase", {
        **CANONICAL_BY_INTENT["add_purchase"], "price": provider_price,
    })
    assert decode_provider_envelope(json.dumps(payload)).arguments["price"] == expected


def test_provider_price_above_maximum_reaches_authoritative_canonical_bounds_check():
    payload = provider_envelope("add_purchase", {
        **CANONICAL_BY_INTENT["add_purchase"], "price": "1000000001",
    })
    with pytest.raises(IntentParserInvalidOutput, match="invalid_price"):
        decode_provider_envelope(json.dumps(payload))


@pytest.mark.parametrize("provider_price", [
    "-35000", "35.5", "35-40 тысяч", "$350", "350 евро", "тридцать пять тысяч",
    "около 35 тысяч", "до 35000", "35к", "35k", "35.5k", "35000 за кофемашину",
    "35 00", "35_000 ₽ extra",
])
def test_provider_purchase_price_rejects_ambiguous_foreign_or_prose_values(provider_price):
    payload = provider_envelope("add_purchase", {
        **CANONICAL_BY_INTENT["add_purchase"], "price": provider_price,
    })
    with pytest.raises(IntentParserInvalidOutput, match="invalid_provider_price"):
        decode_provider_envelope(json.dumps(payload))


def test_invalid_price_log_uses_only_bounded_representation_category(caplog):
    private_price = "35000 за секретную кофемашину"
    content = json.dumps(provider_envelope("add_purchase", {
        **CANONICAL_BY_INTENT["add_purchase"], "price": private_price,
    }))
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response_content(content)))
    with caplog.at_level(logging.WARNING), pytest.raises(IntentParserInvalidOutput):
        run(PolzaIntentParser(api_key="secret", model="openai/gpt-4o-mini", client=client).parse("private", context()))
    run(client.aclose())
    assert "provider_price_category=contains_spaces" in caplog.text
    assert private_price not in caplog.text


@pytest.mark.parametrize(("operation", "logged"), [
    ("show", "show"),
    ("показать личный календарь", "unknown"),
])
def test_invalid_operation_diagnostic_logs_only_safe_bounded_token(operation, logged):
    content = json.dumps(provider_envelope("query_calendar", {
        **CANONICAL_BY_INTENT["query_calendar"], "operation": operation,
    }))
    assert provider_rejection_diagnostics(content, "invalid_operation") == (logged, "unknown")


def test_production_update_calendar_redundant_current_user_owner_is_safe():
    payload = provider_envelope("update_calendar_event", CANONICAL_BY_INTENT["update_calendar_event"], owner="current_user")
    assert decode_provider_envelope(json.dumps(payload)).arguments == CANONICAL_BY_INTENT["update_calendar_event"]


def test_calendar_owner_equivalence_does_not_hide_other_owner_conflict():
    payload = provider_envelope("update_calendar_event", CANONICAL_BY_INTENT["update_calendar_event"], owner="other_user")
    with pytest.raises(IntentParserInvalidOutput, match="irrelevant_non_null_field"):
        decode_provider_envelope(json.dumps(payload))


def test_production_movie_title_is_normalized_to_canonical_query():
    payload = provider_envelope("add_movie_or_tv", {"title": "Дюна"})
    assert decode_provider_envelope(json.dumps(payload)).arguments == {"query": "Дюна"}


def test_movie_title_and_query_are_not_silently_reconciled():
    payload = provider_envelope("add_movie_or_tv", {"title": "Дюна", "query": "Офис"})
    with pytest.raises(IntentParserInvalidOutput, match="conflicting_provider_fields"):
        decode_provider_envelope(json.dumps(payload))


def test_production_afisha_missing_title_remains_fail_closed():
    payload = provider_envelope("add_afisha_event", {"date_expression": "завтра"})
    with pytest.raises(IntentParserInvalidOutput, match="invalid_title"):
        decode_provider_envelope(json.dumps(payload))


def test_production_unsupported_unknown_category_safely_collapses_to_catch_all():
    payload = provider_envelope("unsupported", {"category": "unknown_request"})
    result = decode_provider_envelope(json.dumps(payload))
    assert result.arguments == {"category": "unsupported_domain"}


def test_attach_event_file_minimal_and_metadata_decode_strictly():
    minimal = {"target": "поездка в санаторий", "semantic_type": None, "transport_type": None,
               "origin": None, "destination": None, "date_expression": None, "departure_time": None, "person": None}
    result = decode_intent({"intent": "attach_event_file", "arguments": minimal})
    assert result.intent is IntentKind.ATTACH_EVENT_FILE and result.arguments == minimal
    detailed = {**minimal, "semantic_type": "transport_ticket", "transport_type": "train",
                "origin": "Москва", "destination": "Воронеж", "person": "current_user"}
    assert decode_intent({"intent": "attach_event_file", "arguments": detailed}).arguments == detailed


def test_attach_event_file_departure_time_is_strict_at_canonical_boundary():
    arguments = {"target": "санаторий", "semantic_type": "transport_ticket", "transport_type": "train",
                 "origin": "Москва", "destination": "Воронеж", "date_expression": "31 августа",
                 "departure_time": "08:10", "person": None}
    assert decode_intent({"intent": "attach_event_file", "arguments": arguments}).arguments["departure_time"] == "08:10"
    for invalid in ("8 утра", "24:00", "08:60"):
        with pytest.raises(IntentParserInvalidOutput):
            decode_intent({"intent": "attach_event_file", "arguments": {**arguments, "departure_time": invalid}})


@pytest.mark.parametrize(("field", "value"), [("semantic_type", "receipt"), ("transport_type", "car"), ("person", "everyone")])
def test_attach_event_file_invalid_enums_fail_closed(field, value):
    arguments = {"target": "Санаторий", "semantic_type": None, "transport_type": None,
                 "origin": None, "destination": None, "date_expression": None, "departure_time": None, "person": None}
    arguments[field] = value
    with pytest.raises(IntentParserInvalidOutput):
        decode_intent({"intent": "attach_event_file", "arguments": arguments})


def test_attachment_examples_are_in_provider_prompt():
    assert 'attach_event_file target="поездка в санаторий"' in SYSTEM_PROMPT
    assert 'transport_type="train" origin="Москва" destination="Воронеж"' in SYSTEM_PROMPT
    assert "ссылка на событие в формулировке пользователя" in SYSTEM_PROMPT
    assert "названия из хранилища тебе не передаются" in SYSTEM_PROMPT

@pytest.mark.parametrize("text,intent", [
    ("удали билет в Воронеж", "delete_event_attachment"),
    ("удали обратный билет из Воронежа", "delete_event_attachment"),
    ("удали ваучер к санаторию", "delete_event_attachment"),
    ("у билета в Воронеж время отправления 23:50", "update_event_attachment"),
    ("измени время прибытия билета в Воронеж на 09:40", "update_event_attachment"),
    ("поменяй дату билета в Воронеж на 31 августа", "update_event_attachment"),
    ("у обратного билета дата прибытия 5 сентября", "update_event_attachment"),
])
def test_attachment_mutation_prompt_prevents_event_intent_collision(text, intent):
    assert text in SYSTEM_PROMPT or intent in SYSTEM_PROMPT
    assert f"{intent}=" in SYSTEM_PROMPT
    attachment_rule = SYSTEM_PROMPT[ SYSTEM_PROMPT.index("Для delete_event_attachment"): ]
    assert "НИКОГДА не событие календаря/Афиши" in attachment_rule


def test_decodes_delete_attachment_without_any_storage_identifiers():
    parsed = decode_provider_envelope({"intent": "delete_event_attachment", "arguments": [
        {"name": "semantic_type", "value": "transport_ticket"},
        {"name": "destination", "value": "Воронеж"},
    ]})
    assert parsed.intent is IntentKind.DELETE_EVENT_ATTACHMENT
    assert parsed.arguments["destination"] == "Воронеж"
    assert not ({"id", "parent_id", "file_id"} & parsed.arguments.keys())


def test_decodes_update_attachment_separating_identifier_and_change():
    parsed = decode_provider_envelope({"intent": "update_event_attachment", "arguments": [
        {"name": "semantic_type", "value": "transport_ticket"},
        {"name": "destination", "value": "Воронеж"},
        {"name": "new_arrival_time", "value": "09:40"},
    ]})
    assert parsed.intent is IntentKind.UPDATE_EVENT_ATTACHMENT
    assert parsed.arguments["destination"] == "Воронеж"
    assert parsed.arguments["new_destination"] is None
    assert parsed.arguments["new_arrival_time"] == "09:40"
