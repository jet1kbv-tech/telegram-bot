from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from bot.services.nl_intent import (
    IntentContext, IntentParserInvalidOutput, IntentParserTimeout, IntentParserUnavailable, ParsedIntent,
)
from bot.services.nl_intent_decoder import INTENT_JSON_SCHEMA, decode_provider_envelope

logger = logging.getLogger(__name__)
POLZA_CHAT_COMPLETIONS_URL = "https://polza.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """Ты — классификатор команд Telegram-бота. Верни только JSON по заданной схеме.
Поддерживаются add_movie_or_tv, add_purchase, add_personal_calendar_event, add_afisha_event, no_action, unsupported.
Для фильма верни только query: не придумывай метаданные. Для дат сохраняй исходное русское выражение, приложение разрешит дату само.
owner личного календаря всегда current_user. Если просят календарь другого человека — unsupported/other_user_calendar.
Не объясняй ответ и не добавляй поля. Аргументы для выбранного intent должны присутствовать, необязательные значения — null."""


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
        result = decode_provider_envelope(content)
        logger.info("Polza intent parsed intent=%s latency_ms=%s", result.intent.value, latency_ms)
        return result

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
