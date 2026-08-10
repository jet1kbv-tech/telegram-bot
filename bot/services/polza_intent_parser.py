from __future__ import annotations

import logging
import json
import time
from typing import Any

import httpx

from bot.services.nl_intent import (
    IntentContext, IntentParserInvalidOutput, IntentParserTimeout, IntentParserUnavailable, ParsedIntent,
)
from bot.services.nl_intent_decoder import INTENT_JSON_SCHEMA, decode_provider_envelope

logger = logging.getLogger(__name__)
POLZA_CHAT_COMPLETIONS_URL = "https://polza.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """Классифицируй русскую команду Telegram-бота и верни только строгий JSON заданной ветки schema.

Intents: add_personal_calendar_event/add_afisha_event/add_purchase/add_movie_or_tv; update/delete_purchase, _film, _calendar_event, _afisha_event; read-only query_purchases/query_films/query_calendar/query_afisha; unsupported; no_action. Различай add, update, delete и query как ровно одно действие. Терпи разговорную грамматику, склонения, порядок слов и отсутствие пунктуации. Недостающие значения = null, а не unsupported/no_action. Для update заполняй только явно изменяемые поля; target — название, не ID. Purchase statuses planned/bought; film want/watched.

Личный календарь без явно другого владельца: owner=current_user. Явное изменение чужого календаря: unsupported/other_user_calendar. Query calendar также только current_user и без owner/id; Афиша общая. Отсутствие даты, времени НЕ означает unsupported. Даты/время сохраняй компактно и дословно в *_expression («17.08», «завтра», «в пятницу»), не преобразуй его в ISO. Для фильма/TV извлекай только query. Цена покупки — число рублей («35 тысяч» = 35000).

Query defaults: purchases status=planned priority=any buyer=any, operations list/count/sum; films status=want media_type=any genre=null, operations list/count/random (сериалы=tv, фильмы=movie); calendar/afisha operations list/count/next, target только при поиске названия, date_from/date_to повторяют исходный диапазон либо null.

unsupported — только команда вне доменов, destructive/bulk или чужой календарь. no_action используй только для текста без команды. Все поля выбранной ветки обязательны; необязательные значения null. Не объясняй ответ и не добавляй поля.

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
            "intent_branches": len(INTENT_JSON_SCHEMA["schema"]["oneOf"]),
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
            logger.warning("NL parse provider_error outcome=http_%s latency_ms=%s", response.status_code, latency_ms)
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
            intent, argument_keys = _response_shape(content)
            logger.warning(
                "NL parse validation_error outcome=decode_error reason=%s intent=%s argument_keys=%s latency_ms=%s",
                str(exc) or type(exc).__name__, intent, argument_keys, latency_ms,
            )
            raise
        logger.info("NL parse success outcome=success intent=%s latency_ms=%s", result.intent.value, latency_ms)
        return result

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}


def _response_shape(content: str) -> tuple[str, list[str]]:
    """Return non-sensitive structure only; provider/user values are never logged."""
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return "<unavailable>", []
    if not isinstance(value, dict):
        return "<invalid>", []
    intent = value.get("intent")
    arguments = value.get("arguments")
    return (intent if isinstance(intent, str) else "<invalid>", sorted(arguments) if isinstance(arguments, dict) else [])
