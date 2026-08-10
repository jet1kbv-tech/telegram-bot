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

SYSTEM_PROMPT = """Ты — классификатор и извлекатель аргументов команд Telegram-бота. Верни только JSON по заданной схеме.

Поддержанные добавления:
- add_personal_calendar_event — добавить/запланировать/записать событие в личный календарь пользователя;
- add_afisha_event — добавить событие в общую афишу;
- add_purchase — добавить вещь в покупки;
- add_movie_or_tv — добавить фильм или сериал в список.
Выбирай поддержанный intent по ясно выраженному действию, даже при разговорной фразе, ошибках склонения, другом порядке слов, без пунктуации и с неполными аргументами. Отсутствие даты, времени или другого обязательного значения НЕ означает unsupported: верни nullable поля как null, чтобы бот запросил уточнение.

Для личного календаря фразы «в календарь», «в мой календарь», «мне в календарь» без явно названного другого владельца означают owner=current_user. Только явная просьба изменить личный календарь другого человека — unsupported с category=other_user_calendar. Для дат дословно сохраняй выражение пользователя в date_expression (например, «17.08», «17 августа», «завтра», «в пятницу»); не преобразуй его в ISO и не отвергай intent из-за неполной даты. Аналогично сохраняй выражение времени.
Для фильма/сериала верни только query и не придумывай метаданные. Для add_purchase верни title, числовой price в рублях (например, «35 тысяч» = 35000), priority, link, comment и buyer.

Также поддержаны update/delete_purchase, update/delete_film, update/delete_calendar_event, update/delete_afisha_event. Отличай изменение и удаление от добавления. Для update/delete верни только human-readable target, никогда не ID. В update заполняй только явно запрошенные изменения, остальные nullable поля — null; не подставляй существующие значения. Статусы: purchase planned/bought, film want/watched. Удаление существующей сущности — соответствующий delete intent, не unsupported.

unsupported используй узко: только для ясно выраженной выполнимой команды вне перечисленных возможностей, запрещённой destructive/bulk операции, неподдержанного домена или явного изменения чужого календаря. Не используй unsupported из-за плохой грамматики или недостающих полей. no_action используй только для текста без команды: приветствия, реплики и разговор («привет», «мы устали», «прикольно»). Частично заполненная поддержанная команда — не no_action.

Примеры границ:
«добавь стоматолог в календарь 17.08» -> add_personal_calendar_event, title="стоматолог", date_expression="17.08", owner="current_user";
«добавь кофемашину в покупки за 35 тысяч» -> add_purchase;
«добавь Во все тяжкие в фильмы» -> add_movie_or_tv;
«добавь концерт в афишу 20 сентября» -> add_afisha_event;
«удали концерт из афиши» -> delete_afisha_event;
«перенеси стоматолога на завтра» -> update_calendar_event;
«добавь Саше в календарь встречу» (если Саша не текущий пользователь) -> unsupported/other_user_calendar.

Не объясняй ответ и не добавляй поля. Верни ровно ветку выбранного intent; все её аргументы должны присутствовать, необязательные значения — null."""


class PolzaIntentParser:
    def __init__(self, *, api_key: str, model: str, timeout_seconds: float = 10, client: httpx.AsyncClient | None = None) -> None:
        if not api_key or not model:
            raise ValueError("Polza API key and model are required")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._client = client

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
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(POLZA_CHAT_COMPLETIONS_URL, headers=self._headers(), json=payload)
            else:
                response = await self._client.post(POLZA_CHAT_COMPLETIONS_URL, headers=self._headers(), json=payload, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise IntentParserTimeout("provider_timeout") from exc
        except httpx.HTTPError as exc:
            raise IntentParserUnavailable("provider_unavailable") from exc
        latency_ms = round((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            logger.warning("Polza intent request failed status=%s latency_ms=%s", response.status_code, latency_ms)
            raise IntentParserUnavailable(f"provider_status_{response.status_code}")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise IntentParserInvalidOutput("invalid_provider_response") from exc
        if not isinstance(content, str):
            raise IntentParserInvalidOutput("invalid_provider_content")
        try:
            result = decode_provider_envelope(content)
        except IntentParserInvalidOutput as exc:
            intent, argument_keys = _response_shape(content)
            logger.warning(
                "Polza intent decode failed reason=%s intent=%s argument_keys=%s latency_ms=%s",
                str(exc) or type(exc).__name__, intent, argument_keys, latency_ms,
            )
            raise
        logger.info("Polza intent parsed intent=%s latency_ms=%s", result.intent.value, latency_ms)
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
