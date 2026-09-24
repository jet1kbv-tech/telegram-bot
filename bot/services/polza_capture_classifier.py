"""Polza-backed Universal Capture classifier with privacy-safe diagnostics."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Mapping

import httpx

from bot.services.capture import (CAPTURE_CLASSIFICATION_JSON_SCHEMA, CaptureContext,
                                  CaptureValidationError, NormalizedCapture,
                                  validate_capture_classification)
from bot.services.polza_intent_parser import POLZA_CHAT_COMPLETIONS_URL


logger = logging.getLogger(__name__)
MAX_CAPTURE_PROVIDER_RESPONSE_BYTES = 32 * 1024

SYSTEM_PROMPT = """Классифицируй обычный текст личного Telegram-ассистента. Верни только JSON по schema, без пояснений.

Выбери destination: films, wishlist, purchases, leisure, places, afisha или notes. Извлекай только явно сказанное, не придумывай даты, время, город, ссылки, идентификаторы или владельца. date_expression/time_expression сохраняй словами пользователя, не преобразуй в ISO.

certainty=clear и alternatives=[] для уверенного раздела. certainty=ambiguous и 1–2 уникальных alternatives для реальной неоднозначности; основной и альтернативы должны быть равно правдоподобны.

Границы:
- purchases: конкретное действие купить/заказать/забрать/заменить/приобрести.
- wishlist: на день рождения, хочу в подарок, подарок мне, мечтаю, когда-нибудь хочу себе.
- Голое «хочу новые наушники/новый штатив» неоднозначно между wishlist и purchases; не отправляй такое в notes.
- leisure: идея активности без расписания («хочу сходить на мастер-класс», «хочу на квест»).
- afisha: фактическое событие/активность с конкретной датой или временем. Без даты событие может быть неоднозначно leisure/afisha. Не придумывай недостающее время.
- places: именованное заведение или место как объект («хочу сходить в бар X»). Неименованный «новый бар» может быть places/leisure. Город указывай только явно. Только явное «Москва/в Москве» означает Москву; Китай-город, Патрики, Хамовники, ВДНХ и Третьяковка сами по себе не доказывают город.
- films: намерение посмотреть/сохранить названный фильм или сериал. Обсуждение фильма («почему Паразиты получили Оскар») — notes.
- notes: личная мысль без подходящего предметного раздела. Для notes поле text обязано дословно совпадать со всем пользовательским текстом.
- «напомнить себе» без даты/времени может быть notes.

reason_code выбирай строго по смыслу schema. В candidate верни все поля schema: неприменимые и явно не указанные необязательные значения заполни null. Никогда не возвращай id, owner, actor, user_id, buyer, status, visibility, external_id, tmdb_id, city_id, source_id или added_by."""


_PROVIDER_CANDIDATE_FIELDS = frozenset(
    CAPTURE_CLASSIFICATION_JSON_SCHEMA["schema"]["properties"]["candidate"]["properties"]
)


def _normalize_provider_output(raw: Any) -> dict[str, Any]:
    """Convert the all-required provider wire object to the strict tagged app contract."""
    if not isinstance(raw, dict) or set(raw) != {
        "contract_version", "destination", "certainty", "alternatives", "reason_code", "candidate"
    }:
        raise CaptureValidationError("invalid_provider_fields")
    candidate = raw.get("candidate")
    if not isinstance(candidate, dict) or set(candidate) != _PROVIDER_CANDIDATE_FIELDS:
        raise CaptureValidationError("invalid_provider_candidate_fields")
    return {**raw, "candidate": {key: value for key, value in candidate.items()
                                  if key == "kind" or value is not None}}


class PolzaCaptureClassifier:
    def __init__(self, *, api_key: str, model: str, timeout_seconds: float = 10,
                 client: httpx.AsyncClient | None = None) -> None:
        if not api_key or not model:
            raise ValueError("Polza API key and model are required")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._client = client
        logger.info("Capture classifier configured contract_version=2")

    async def classify(self, capture: NormalizedCapture,
                       context: CaptureContext) -> Mapping[str, Any]:
        payload = {
            "model": self._model,
            "stream": False,
            "temperature": 0,
            "max_tokens": 1200,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"Локальные дата и время: {context.local_now.isoformat(timespec='minutes')}; "
                    f"часовой пояс: {context.timezone}.\nТекст: {capture.text}"
                )},
            ],
            "response_format": {"type": "json_schema",
                                "json_schema": CAPTURE_CLASSIFICATION_JSON_SCHEMA},
        }
        started = time.monotonic()
        logger.info("Capture classification started outcome=started")
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(POLZA_CHAT_COMPLETIONS_URL,
                        headers=self._headers(), json=payload)
            else:
                response = await self._client.post(POLZA_CHAT_COMPLETIONS_URL,
                    headers=self._headers(), json=payload, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            logger.warning("Capture classification failed category=timeout latency_ms=%s",
                           self._latency(started))
            raise CaptureValidationError("provider_timeout") from exc
        except httpx.HTTPError as exc:
            logger.warning("Capture classification failed category=transport latency_ms=%s",
                           self._latency(started))
            raise CaptureValidationError("provider_unavailable") from exc
        if response.status_code >= 400:
            logger.warning("Capture classification failed category=http_status status=%s latency_ms=%s",
                           response.status_code, self._latency(started))
            raise CaptureValidationError("provider_status")
        if len(response.content) > MAX_CAPTURE_PROVIDER_RESPONSE_BYTES:
            logger.warning("Capture classification failed category=oversized_response latency_ms=%s",
                           self._latency(started))
            raise CaptureValidationError("provider_response_too_large")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_CAPTURE_PROVIDER_RESPONSE_BYTES:
                raise ValueError("invalid content")
            raw = _normalize_provider_output(json.loads(content))
            classification = validate_capture_classification(raw, source=capture)
        except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError,
                CaptureValidationError) as exc:
            logger.warning("Capture classification failed category=invalid_output latency_ms=%s",
                           self._latency(started))
            raise CaptureValidationError("invalid_provider_output") from exc
        logger.info("Capture classification succeeded destination=%s certainty=%s reason_code=%s latency_ms=%s",
                    classification.destination, classification.certainty,
                    classification.reason_code, self._latency(started))
        return raw

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _latency(started: float) -> int:
        return round((time.monotonic() - started) * 1000)
