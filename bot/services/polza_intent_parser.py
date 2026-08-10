from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from bot.services.nl_intent import (
    IntentContext, IntentParserInvalidOutput, IntentParserTimeout, IntentParserUnavailable, ParsedIntent,
)
from bot.services.nl_intent_decoder import (
    INTENT_JSON_SCHEMA, decode_provider_envelope, provider_rejection_diagnostics, provider_rejection_shape,
)

logger = logging.getLogger(__name__)
POLZA_CHAT_COMPLETIONS_URL = "https://polza.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """Классифицируй русскую команду Telegram-бота и верни только строгий JSON по schema.

Выбери один intent. arguments — компактный список только явно извлечённых смысловых полей: {"name":"имя","value":"строковое значение"}. Не добавляй отсутствующие или посторонние поля; null-записей нет. Числа также передавай строкой.
Поля intent:
add_movie_or_tv=query; add_purchase=title,price,priority,link,comment,buyer; add_personal_calendar_event=title,date_expression,time_expression,end_time_expression,comment,owner; add_afisha_event=title,place,date_expression,time_expression,end_date_expression,end_time_expression,link;
update_purchase=target,title,price,priority,link,comment,buyer,status; delete_purchase=target; update_film=target,status,comment; delete_film=target; update_calendar_event=target,title,date_expression,time_expression; delete_calendar_event=target; update_afisha_event=target,title,date_expression,time_expression; delete_afisha_event=target;
query_purchases=status,priority,buyer,operation; query_films=status,media_type,genre,operation; query_calendar/query_afisha=date_from,date_to,target,operation; unsupported=category; no_action=без полей.

Intents: add_personal_calendar_event/add_afisha_event/add_purchase/add_movie_or_tv; update/delete_purchase, _film, _calendar_event, _afisha_event; read-only query_purchases/query_films/query_calendar/query_afisha; unsupported; no_action. Различай add, update, delete и query как ровно одно действие. Терпи разговорную грамматику, склонения, порядок слов и отсутствие пунктуации. Недостающие значения = null, а не unsupported/no_action. Для update заполняй только явно изменяемые поля; target — название, не ID. Purchase statuses planned/bought; film want/watched.

Личный календарь без явно другого владельца: owner=current_user. Явное изменение чужого календаря: unsupported/other_user_calendar. Query calendar также только current_user и без owner/id; Афиша общая. Отсутствие даты, времени НЕ означает unsupported. Даты/время сохраняй компактно и дословно в *_expression («17.08», «завтра», «в пятницу»), не преобразуй его в ISO. Для фильма/TV извлекай только query. Цену покупки передавай только цифрами без валюты («35 тысяч» = 35000).

Query defaults: purchases status=planned priority=any buyer=any, operations list/count/sum; films status=want media_type=any genre=null, operations list/count/random (сериалы=tv, фильмы=movie); calendar/afisha operation строго list/count/next (обычный показ списка = list), target только при поиске названия, date_from/date_to повторяют исходный диапазон либо null.

unsupported — только команда вне доменов, destructive/bulk или чужой календарь. category: destructive, other_user_calendar, bulk, unsupported_domain или conversation. no_action используй только для текста без команды. Обязательные поля выбранной ветки добавь в arguments; необязательные отсутствующие поля пропусти. Не объясняй ответ и не добавляй поля.

Граничные примеры:
«добавь стоматолог в календарь 17.08» -> add_personal_calendar_event, title="стоматолог", date_expression="17.08", owner="current_user";
«добавь кофемашину в покупки за 35 тысяч» -> add_purchase;
«добавь Во все тяжкие в фильмы» -> add_movie_or_tv;
«добавь концерт в афишу 20 сентября» -> add_afisha_event;
«удали концерт из афиши» -> delete_afisha_event; «перенеси стоматолога на завтра» -> update_calendar_event;
«добавь Саше в календарь встречу» (если Саша не текущий пользователь) -> unsupported/other_user_calendar.
«что у нас в покупках?» -> query_purchases status=planned; «какие комедии мы ещё не смотрели?» -> query_films status=want genre="Комедия";
«что у меня завтра?» -> query_calendar date_from=date_to="завтра";
«что в афише в августе?» -> query_afisha date_from=date_to="в августе"."""


def payload_diagnostics() -> dict[str, int]:
    schema = json.dumps(INTENT_JSON_SCHEMA, ensure_ascii=False, separators=(",", ":"))
    static = json.dumps({"messages": [{"role": "system", "content": SYSTEM_PROMPT}],
                         "response_format": {"type": "json_schema", "json_schema": INTENT_JSON_SCHEMA}},
                        ensure_ascii=False, separators=(",", ":"))
    return {"prompt_chars": len(SYSTEM_PROMPT), "prompt_bytes": len(SYSTEM_PROMPT.encode()),
            "schema_bytes": len(schema.encode()), "static_payload_bytes": len(static.encode()),
            "intent_branches": len(INTENT_JSON_SCHEMA["schema"]["properties"]["intent"]["enum"]),
            "few_shot_examples": SYSTEM_PROMPT.partition("Граничные примеры:")[2].count("->")}


class PolzaIntentParser:
    def __init__(self, *, api_key: str, model: str, timeout_seconds: float = 10, client: httpx.AsyncClient | None = None) -> None:
        if not api_key or not model:
            raise ValueError("Polza API key and model are required")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._client = client
        metrics = payload_diagnostics()
        logger.info("NL parser configured prompt_bytes=%s schema_bytes=%s static_payload_bytes=%s intent_branches=%s few_shots=%s",
                    metrics["prompt_bytes"], metrics["schema_bytes"], metrics["static_payload_bytes"],
                    metrics["intent_branches"], metrics["few_shot_examples"])

    async def parse(self, text: str, context: IntentContext) -> ParsedIntent:
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"Локальные дата и время: {context.local_now.isoformat(timespec='minutes')}; "
                    f"часовой пояс: {context.timezone}; раздел: {context.active_section or 'menu'}.\nКоманда: {text}"
                )},
            ],
            "response_format": {"type": "json_schema", "json_schema": INTENT_JSON_SCHEMA},
        }
        started = time.monotonic()
        logger.info("NL parse started outcome=started")
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(POLZA_CHAT_COMPLETIONS_URL, headers=self._headers(), json=payload)
            else:
                response = await self._client.post(POLZA_CHAT_COMPLETIONS_URL, headers=self._headers(), json=payload, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            logger.warning("NL parse timeout outcome=timeout latency_ms=%s", round((time.monotonic() - started) * 1000))
            raise IntentParserTimeout("provider_timeout") from exc
        except httpx.HTTPError as exc:
            logger.warning("NL parse provider_error outcome=transport_error latency_ms=%s", round((time.monotonic() - started) * 1000))
            raise IntentParserUnavailable("provider_unavailable") from exc
        latency_ms = round((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            provider_type, provider_code, reason = _safe_provider_error(response)
            logger.warning(
                "NL parse provider_error outcome=http_%s provider_type=%s provider_code=%s reason=%s latency_ms=%s",
                response.status_code, provider_type, provider_code, reason, latency_ms,
            )
            raise IntentParserUnavailable(f"provider_status_{response.status_code}")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("NL parse validation_error outcome=invalid_envelope latency_ms=%s", latency_ms)
            raise IntentParserInvalidOutput("invalid_provider_response") from exc
        if not isinstance(content, str):
            logger.warning("NL parse validation_error outcome=invalid_content latency_ms=%s", latency_ms)
            raise IntentParserInvalidOutput("invalid_provider_content")
        try:
            result = decode_provider_envelope(content)
        except IntentParserInvalidOutput as exc:
            intent, conflicting_fields = provider_rejection_shape(content)
            provider_operation, price_category = provider_rejection_diagnostics(content, str(exc))
            provider_priority = provider_priority_diagnostic(content, str(exc))
            logger.warning(
                "NL parse normalization_rejection intent=%s reason=%s conflicting_fields=%s "
                "provider_operation=%s provider_price_category=%s provider_priority=%s latency_ms=%s",
                intent, str(exc) or type(exc).__name__, conflicting_fields,
                provider_operation, price_category, provider_priority, latency_ms,
            )
            raise
        logger.info("NL parse success outcome=success intent=%s latency_ms=%s", result.intent.value, latency_ms)
        return result

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}


_SAFE_ERROR_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_SAFE_PROVIDER_PRIORITY = re.compile(r"[A-Za-z0-9_-]{1,40}")
_SCHEMA_KEYWORDS = ("oneOf", "anyOf", "additionalProperties", "required", "response_format", "json_schema")


def provider_priority_diagnostic(raw: str, reason: str) -> str:
    """Return only a bounded priority token/category for relevant rejections."""
    if reason not in {"invalid_priority", "invalid_query_arguments", "invalid_argument_entry"}:
        return "unknown"
    try:
        value = json.loads(raw)
        items = value.get("arguments") if isinstance(value, dict) else None
    except (json.JSONDecodeError, TypeError):
        return "unknown"
    if not isinstance(items, list):
        return "unknown"
    priorities = [
        item.get("value")
        for item in items
        if isinstance(item, dict) and item.get("name") == "priority"
    ]
    if not priorities:
        return "missing"
    if len(priorities) != 1:
        return "unknown"
    priority = priorities[0]
    if priority is None:
        return "null"
    if isinstance(priority, str) and _SAFE_PROVIDER_PRIORITY.fullmatch(priority):
        return priority
    return "unknown"


def _safe_provider_error(response: httpx.Response) -> tuple[str, str, str]:
    """Extract only bounded identifiers and a normalized reason, never provider text."""
    try:
        body = response.json()
    except (ValueError, TypeError):
        return "unknown", "unknown", "unclassified"
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return "unknown", "unknown", "unclassified"

    def token(name: str) -> str:
        value = error.get(name)
        return value if isinstance(value, str) and _SAFE_ERROR_TOKEN.fullmatch(value) else "unknown"

    message = error.get("message")
    if not isinstance(message, str) or len(message) > 4000:
        reason = "unclassified"
    elif any(keyword.lower() in message.lower() for keyword in _SCHEMA_KEYWORDS):
        reason = "invalid_structured_output_schema"
    elif "model" in message.lower():
        reason = "model_restriction"
    elif "response format" in message.lower():
        reason = "invalid_response_format"
    else:
        reason = "provider_rejected_request"
    return token("type"), token("code"), reason
