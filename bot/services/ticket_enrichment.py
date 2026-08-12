"""Privacy-bounded multimodal extraction for transport tickets only."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime
import json
import logging
from typing import Any

import httpx

POLZA_CHAT_COMPLETIONS_URL = "https://polza.ai/api/v1/chat/completions"
logger = logging.getLogger(__name__)

TICKET_SCHEMA = {
    "name": "ticket_enrichment",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "origin": {"type": ["string", "null"]},
            "destination": {"type": ["string", "null"]},
            "date": {"type": ["string", "null"], "pattern": r"^\d{4}-\d{2}-\d{2}$"},
            "departure_time": {"type": ["string", "null"], "pattern": r"^\d{2}:\d{2}$"},
        },
        "required": ["origin", "destination", "date", "departure_time"],
    },
}

PROMPT = """Извлеки только данные ОДНОЙ поездки из транспортного билета.
origin — город/станция/аэропорт отправления; destination — прибытия; date — именно дата
ОТПРАВЛЕНИЯ YYYY-MM-DD; departure_time — именно время ОТПРАВЛЕНИЯ HH:MM. Не путай их с
датой покупки, прибытием, посадкой или окончанием регистрации. Для ночного рейса используй
дату отправления. Если значение не видно или сомнительно — null. Не выдумывай. Если документ
содержит несколько самостоятельных поездок/маршрутов — верни null для всех полей. Игнорируй
имена пассажиров, паспортные данные, номера, места, цены, штрихкоды и QR."""


class TicketEnrichmentError(RuntimeError): pass
class TicketEnrichmentTimeout(TicketEnrichmentError): pass
class TicketEnrichmentUnavailable(TicketEnrichmentError): pass
class TicketEnrichmentInvalidOutput(TicketEnrichmentError): pass


@dataclass(frozen=True)
class TicketEnrichmentResult:
    origin: str | None
    destination: str | None
    date: str | None
    departure_time: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {"origin": self.origin, "destination": self.destination,
                "date": self.date, "departure_time": self.departure_time}

    @property
    def useful(self) -> bool:
        return any(self.as_dict().values())


def decode_ticket_enrichment(raw: str) -> TicketEnrichmentResult:
    try: value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc: raise TicketEnrichmentInvalidOutput("malformed_json") from exc
    fields = {"origin", "destination", "date", "departure_time"}
    if not isinstance(value, dict) or set(value) != fields:
        raise TicketEnrichmentInvalidOutput("invalid_fields")
    cleaned: dict[str, str | None] = {}
    for field in ("origin", "destination"):
        item = value[field]
        if item is not None and not isinstance(item, str): raise TicketEnrichmentInvalidOutput("invalid_text")
        item = item.strip() if isinstance(item, str) else None
        if item and len(item) > 120: raise TicketEnrichmentInvalidOutput("text_too_long")
        cleaned[field] = item or None
    for field, fmt in (("date", "%Y-%m-%d"), ("departure_time", "%H:%M")):
        item = value[field]
        if item is not None and not isinstance(item, str): raise TicketEnrichmentInvalidOutput(f"invalid_{field}")
        if item is not None:
            try: item = datetime.strptime(item, fmt).strftime(fmt)
            except ValueError as exc: raise TicketEnrichmentInvalidOutput(f"invalid_{field}") from exc
        cleaned[field] = item
    return TicketEnrichmentResult(**cleaned)


class PolzaTicketEnricher:
    def __init__(self, *, api_key: str, model: str, timeout_seconds: float = 25,
                 client: httpx.AsyncClient | None = None) -> None:
        if not api_key or not model: raise ValueError("Polza API key and attachment model are required")
        self._api_key, self._model, self._timeout, self._client = api_key, model, timeout_seconds, client

    def build_payload(self, content: bytes, media_type: str, *, mime_type: str,
                      local_date: date, timezone: str, event_date: str | None) -> dict[str, Any]:
        encoded = base64.b64encode(content).decode("ascii")
        context = (f"Контекст: локальная дата {local_date.isoformat()}, часовой пояс {timezone}, "
                   f"дата родительского события {event_date or 'не указана'}. Дата события — только "
                   "контекст и не должна копироваться при отсутствии даты на билете.")
        if media_type == "image":
            part = {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}
        elif media_type == "pdf":
            part = {"type": "file", "file": {"filename": "ticket.pdf",
                    "file_data": f"data:application/pdf;base64,{encoded}"}}
        else: raise ValueError("unsupported_media")
        return {"model": self._model, "stream": False, "temperature": 0,
                "messages": [{"role": "user", "content": [{"type": "text", "text": f"{PROMPT}\n{context}"}, part]}],
                "response_format": {"type": "json_schema", "json_schema": TICKET_SCHEMA}}

    async def enrich(self, content: bytes, media_type: str, *, mime_type: str,
                     local_date: date, timezone: str, event_date: str | None = None) -> TicketEnrichmentResult:
        payload = self.build_payload(content, media_type, mime_type=mime_type, local_date=local_date,
                                     timezone=timezone, event_date=event_date)
        logger.info("ticket_enrichment_started media_type=%s byte_bucket=%s", media_type, _byte_bucket(len(content)))
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(POLZA_CHAT_COMPLETIONS_URL, headers=self._headers(), json=payload)
            else:
                response = await self._client.post(POLZA_CHAT_COMPLETIONS_URL, headers=self._headers(), json=payload,
                                                   timeout=self._timeout)
        except httpx.TimeoutException as exc:
            logger.warning("ticket_enrichment_failed reason=timeout"); raise TicketEnrichmentTimeout("timeout") from exc
        except httpx.HTTPError as exc:
            logger.warning("ticket_enrichment_failed reason=transport"); raise TicketEnrichmentUnavailable("transport") from exc
        if response.status_code >= 400:
            logger.warning("ticket_enrichment_failed reason=http_status status=%s", response.status_code)
            raise TicketEnrichmentUnavailable("provider_rejected")
        try: raw = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("ticket_enrichment_failed reason=envelope"); raise TicketEnrichmentInvalidOutput("envelope") from exc
        try: result = decode_ticket_enrichment(raw)
        except TicketEnrichmentInvalidOutput:
            logger.warning("ticket_enrichment_failed reason=validation"); raise
        logger.info("ticket_enrichment_success extracted_field_count=%s", sum(v is not None for v in result.as_dict().values()))
        return result

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}


def _byte_bucket(size: int) -> str:
    if size < 1024 * 1024: return "under_1mb"
    if size < 4 * 1024 * 1024: return "1_to_4mb"
    return "4mb_or_more"
