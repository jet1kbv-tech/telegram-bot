"""Storage-independent models and validation for Universal Capture."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol


CAPTURE_CONTRACT_VERSION = 1
MAX_CAPTURE_TEXT_LENGTH = 4000
CAPTURE_KINDS = frozenset({"text"})
CAPTURE_DESTINATIONS = frozenset({"notes"})
CAPTURE_ACTIONS = frozenset({"create"})
CAPTURE_REASON_CODES = frozenset({"personal_thought"})

CAPTURE_CLASSIFICATION_JSON_SCHEMA = {
    "name": "universal_capture_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "contract_version": {"type": "integer", "const": CAPTURE_CONTRACT_VERSION},
            "destination": {"type": "string", "enum": sorted(CAPTURE_DESTINATIONS)},
            "action": {"type": "string", "enum": sorted(CAPTURE_ACTIONS)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason_code": {"type": "string", "enum": sorted(CAPTURE_REASON_CODES)},
            "candidate": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"text": {"type": "string", "minLength": 1,
                                          "maxLength": MAX_CAPTURE_TEXT_LENGTH}},
                "required": ["text"],
            },
        },
        "required": ["contract_version", "destination", "action", "confidence",
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
    action: str
    confidence: float
    reason_code: str
    candidate_text: str


class CaptureClassifier(Protocol):
    async def classify(self, capture: NormalizedCapture,
                       context: CaptureContext) -> Mapping[str, Any]: ...


def normalize_text_capture(text: str) -> NormalizedCapture:
    """Normalize only Telegram text; perform no I/O and retain the user's wording."""
    if not isinstance(text, str):
        raise CaptureValidationError("invalid_text")
    value = text.strip()
    if not value:
        raise CaptureValidationError("blank_text")
    if len(value) > MAX_CAPTURE_TEXT_LENGTH:
        raise CaptureValidationError("text_too_long")
    return NormalizedCapture(kind="text", text=value)


def validate_capture_classification(raw: Mapping[str, Any], *,
                                    source: NormalizedCapture) -> CaptureClassification:
    """Decode the strict v1 Notes-only contract and preserve source text exactly."""
    if (not isinstance(source, NormalizedCapture) or source.kind not in CAPTURE_KINDS
            or not source.text or len(source.text) > MAX_CAPTURE_TEXT_LENGTH):
        raise CaptureValidationError("invalid_source")
    expected = {"contract_version", "destination", "action", "confidence", "reason_code", "candidate"}
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise CaptureValidationError("invalid_fields")
    if raw["contract_version"] != CAPTURE_CONTRACT_VERSION or isinstance(raw["contract_version"], bool):
        raise CaptureValidationError("invalid_contract_version")
    if raw["destination"] not in CAPTURE_DESTINATIONS:
        raise CaptureValidationError("invalid_destination")
    if raw["action"] not in CAPTURE_ACTIONS:
        raise CaptureValidationError("invalid_action")
    confidence = raw["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise CaptureValidationError("invalid_confidence")
    confidence = float(confidence)
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise CaptureValidationError("invalid_confidence")
    if raw["reason_code"] not in CAPTURE_REASON_CODES:
        raise CaptureValidationError("invalid_reason_code")
    candidate = raw["candidate"]
    if not isinstance(candidate, Mapping) or set(candidate) != {"text"}:
        raise CaptureValidationError("invalid_candidate")
    candidate_text = candidate["text"]
    if not isinstance(candidate_text, str) or not candidate_text.strip() or len(candidate_text) > MAX_CAPTURE_TEXT_LENGTH:
        raise CaptureValidationError("invalid_candidate_text")
    # Notes capture is deliberately lossless: a classifier cannot summarize or
    # rewrite a private thought before the user confirms it.
    if candidate_text != source.text:
        raise CaptureValidationError("candidate_text_changed")
    return CaptureClassification(CAPTURE_CONTRACT_VERSION, "notes", "create", confidence,
                                 str(raw["reason_code"]), candidate_text)


class NotesCaptureClassifier:
    """AI-01A's deterministic Notes classifier after NL has returned NO_ACTION."""

    async def classify(self, capture: NormalizedCapture,
                       context: CaptureContext) -> Mapping[str, Any]:
        del context
        return {"contract_version": CAPTURE_CONTRACT_VERSION, "destination": "notes",
                "action": "create", "confidence": 1.0, "reason_code": "personal_thought",
                "candidate": {"text": capture.text}}
