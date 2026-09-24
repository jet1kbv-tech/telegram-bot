"""Storage-independent models and strict validation for Universal Capture."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol


CAPTURE_CONTRACT_VERSION = 2
MAX_CAPTURE_TEXT_LENGTH = 4000
MAX_CAPTURE_TITLE_LENGTH = 300
MAX_CAPTURE_CITY_LENGTH = 200
MAX_CAPTURE_EXPRESSION_LENGTH = 200
MAX_CAPTURE_COMMENT_LENGTH = 1000
MAX_CAPTURE_LINK_LENGTH = 2048
CAPTURE_KINDS = frozenset({"text"})
CAPTURE_DESTINATIONS = frozenset(
    {"films", "wishlist", "purchases", "leisure", "places", "afisha", "notes"}
)
CAPTURE_CERTAINTIES = frozenset({"clear", "ambiguous"})
CAPTURE_REASON_CODES = frozenset({
    "film_watch_intent", "purchase_intent", "wishlist_intent", "item_desire_ambiguous",
    "leisure_idea", "scheduled_event", "activity_event_ambiguous", "place_intent",
    "place_activity_ambiguous", "personal_thought",
})


def _string(max_length: int, *, nullable: bool = False) -> dict[str, Any]:
    value: dict[str, Any] = {"type": ["string", "null"] if nullable else "string",
                             "minLength": 1, "maxLength": max_length}
    return value


# Polza's OpenAI-compatible strict Structured Outputs boundary follows the
# subset already used by the NL parser: every object property is required,
# optional semantic values are nullable, and discriminator oneOf/const shapes
# are avoided. The provider wire candidate is normalized to the smaller tagged
# application candidate before validate_capture_classification sees it.
_PROVIDER_CANDIDATE_PROPERTIES: dict[str, Any] = {
    "kind": {"type": "string", "enum": sorted(CAPTURE_DESTINATIONS)},
    "query": _string(MAX_CAPTURE_TITLE_LENGTH, nullable=True),
    "title": _string(MAX_CAPTURE_TITLE_LENGTH, nullable=True),
    "price": {"type": ["integer", "null"], "minimum": 0},
    "name": _string(MAX_CAPTURE_TITLE_LENGTH, nullable=True),
    "city_name": _string(MAX_CAPTURE_CITY_LENGTH, nullable=True),
    "place": _string(MAX_CAPTURE_TITLE_LENGTH, nullable=True),
    "date_expression": _string(MAX_CAPTURE_EXPRESSION_LENGTH, nullable=True),
    "time_expression": _string(MAX_CAPTURE_EXPRESSION_LENGTH, nullable=True),
    "end_date_expression": _string(MAX_CAPTURE_EXPRESSION_LENGTH, nullable=True),
    "end_time_expression": _string(MAX_CAPTURE_EXPRESSION_LENGTH, nullable=True),
    "link": _string(MAX_CAPTURE_LINK_LENGTH, nullable=True),
    "comment": _string(MAX_CAPTURE_COMMENT_LENGTH, nullable=True),
    "text": _string(MAX_CAPTURE_TEXT_LENGTH, nullable=True),
}


CAPTURE_CLASSIFICATION_JSON_SCHEMA = {
    "name": "universal_capture_v2",
    "strict": True,
    "schema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "contract_version": {"type": "integer", "enum": [CAPTURE_CONTRACT_VERSION]},
            "destination": {"type": "string", "enum": sorted(CAPTURE_DESTINATIONS)},
            "certainty": {"type": "string", "enum": sorted(CAPTURE_CERTAINTIES)},
            "alternatives": {"type": "array", "items": {"type": "string", "enum": sorted(CAPTURE_DESTINATIONS)},
                             "minItems": 0, "maxItems": 2, "uniqueItems": True},
            "reason_code": {"type": "string", "enum": sorted(CAPTURE_REASON_CODES)},
            "candidate": {
                "type": "object", "additionalProperties": False,
                "properties": _PROVIDER_CANDIDATE_PROPERTIES,
                "required": list(_PROVIDER_CANDIDATE_PROPERTIES),
            },
        },
        "required": ["contract_version", "destination", "certainty", "alternatives",
                     "reason_code", "candidate"],
    },
}


class CaptureValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NormalizedCapture:
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class CaptureContext:
    local_now: datetime
    timezone: str


@dataclass(frozen=True, slots=True)
class CaptureClassification:
    contract_version: int
    destination: str
    certainty: str
    alternatives: tuple[str, ...]
    reason_code: str
    candidate: Mapping[str, Any]

    @property
    def candidate_text(self) -> str:
        """Compatibility accessor for the lossless Notes mutation path."""
        return str(self.candidate.get("text") or "")


class CaptureClassifier(Protocol):
    async def classify(self, capture: NormalizedCapture,
                       context: CaptureContext) -> Mapping[str, Any]: ...


def normalize_text_capture(text: str) -> NormalizedCapture:
    if not isinstance(text, str):
        raise CaptureValidationError("invalid_text")
    value = text.strip()
    if not value:
        raise CaptureValidationError("blank_text")
    if len(value) > MAX_CAPTURE_TEXT_LENGTH:
        raise CaptureValidationError("text_too_long")
    return NormalizedCapture(kind="text", text=value)


_CANDIDATE_FIELDS = {
    "films": ({"kind", "query"}, {"kind", "query"}),
    "purchases": ({"kind", "title", "price", "link", "comment"}, {"kind", "title"}),
    "wishlist": ({"kind", "title", "link", "comment"}, {"kind", "title"}),
    "leisure": ({"kind", "title", "comment"}, {"kind", "title"}),
    "places": ({"kind", "name", "city_name", "link", "comment"}, {"kind", "name"}),
    "afisha": ({"kind", "title", "place", "date_expression", "time_expression",
                "end_date_expression", "end_time_expression", "link"}, {"kind", "title"}),
    "notes": ({"kind", "text"}, {"kind", "text"}),
}
_FIELD_LIMITS = {
    "query": MAX_CAPTURE_TITLE_LENGTH, "title": MAX_CAPTURE_TITLE_LENGTH,
    "name": MAX_CAPTURE_TITLE_LENGTH, "city_name": MAX_CAPTURE_CITY_LENGTH,
    "place": MAX_CAPTURE_TITLE_LENGTH, "date_expression": MAX_CAPTURE_EXPRESSION_LENGTH,
    "time_expression": MAX_CAPTURE_EXPRESSION_LENGTH,
    "end_date_expression": MAX_CAPTURE_EXPRESSION_LENGTH,
    "end_time_expression": MAX_CAPTURE_EXPRESSION_LENGTH, "link": MAX_CAPTURE_LINK_LENGTH,
    "comment": MAX_CAPTURE_COMMENT_LENGTH, "text": MAX_CAPTURE_TEXT_LENGTH,
}


def validate_capture_classification(raw: Mapping[str, Any], *,
                                    source: NormalizedCapture) -> CaptureClassification:
    """Decode strict v2 output; classifier suggestions never include identity fields."""
    if (not isinstance(source, NormalizedCapture) or source.kind not in CAPTURE_KINDS
            or not source.text or len(source.text) > MAX_CAPTURE_TEXT_LENGTH):
        raise CaptureValidationError("invalid_source")
    expected = {"contract_version", "destination", "certainty", "alternatives",
                "reason_code", "candidate"}
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise CaptureValidationError("invalid_fields")
    if raw["contract_version"] != CAPTURE_CONTRACT_VERSION or isinstance(raw["contract_version"], bool):
        raise CaptureValidationError("invalid_contract_version")
    destination = raw["destination"]
    if destination not in CAPTURE_DESTINATIONS:
        raise CaptureValidationError("invalid_destination")
    certainty = raw["certainty"]
    if certainty not in CAPTURE_CERTAINTIES:
        raise CaptureValidationError("invalid_certainty")
    alternatives = raw["alternatives"]
    if (not isinstance(alternatives, list) or len(alternatives) > 2
            or any(item not in CAPTURE_DESTINATIONS for item in alternatives)
            or len(set(alternatives)) != len(alternatives) or destination in alternatives):
        raise CaptureValidationError("invalid_alternatives")
    if (certainty == "clear" and alternatives) or (certainty == "ambiguous" and not 1 <= len(alternatives) <= 2):
        raise CaptureValidationError("invalid_certainty_alternatives")
    if raw["reason_code"] not in CAPTURE_REASON_CODES:
        raise CaptureValidationError("invalid_reason_code")

    candidate = raw["candidate"]
    if not isinstance(candidate, Mapping):
        raise CaptureValidationError("invalid_candidate")
    allowed, required = _CANDIDATE_FIELDS[destination]
    if not required <= set(candidate) or not set(candidate) <= allowed or candidate.get("kind") != destination:
        raise CaptureValidationError("invalid_candidate_fields")
    for field, limit in _FIELD_LIMITS.items():
        if field not in candidate or candidate[field] is None:
            continue
        value = candidate[field]
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise CaptureValidationError(f"invalid_candidate_{field}")
    if "price" in candidate and candidate["price"] is not None:
        price = candidate["price"]
        if isinstance(price, bool) or not isinstance(price, int) or price < 0:
            raise CaptureValidationError("invalid_candidate_price")
    if destination == "notes" and candidate["text"] != source.text:
        raise CaptureValidationError("candidate_text_changed")
    return CaptureClassification(CAPTURE_CONTRACT_VERSION, destination, certainty,
                                 tuple(alternatives), str(raw["reason_code"]), dict(candidate))


class NotesCaptureClassifier:
    """Deterministic compatibility classifier used when no provider is configured."""

    async def classify(self, capture: NormalizedCapture,
                       context: CaptureContext) -> Mapping[str, Any]:
        del context
        return {"contract_version": CAPTURE_CONTRACT_VERSION, "destination": "notes",
                "certainty": "clear", "alternatives": [], "reason_code": "personal_thought",
                "candidate": {"kind": "notes", "text": capture.text}}
