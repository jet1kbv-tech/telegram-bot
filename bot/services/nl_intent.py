from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, TypeAlias


class IntentKind(str, Enum):
    ADD_MOVIE_OR_TV = "add_movie_or_tv"
    ADD_PURCHASE = "add_purchase"
    ADD_PERSONAL_CALENDAR_EVENT = "add_personal_calendar_event"
    ADD_AFISHA_EVENT = "add_afisha_event"
    NO_ACTION = "no_action"
    UNSUPPORTED = "unsupported"

    def __str__(self) -> str:
        """Return the serialized value when rendered as text."""
        return self.value


@dataclass(frozen=True, slots=True)
class IntentContext:
    actor_key: str
    local_now: datetime
    timezone: str
    active_section: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedIntent:
    intent: IntentKind
    arguments: dict[str, Any]


class IntentParserError(Exception):
    pass


class IntentParserUnavailable(IntentParserError):
    pass


class IntentParserTimeout(IntentParserError):
    pass


class IntentParserInvalidOutput(IntentParserError):
    pass


class IntentParser(Protocol):
    async def parse(self, text: str, context: IntentContext) -> ParsedIntent: ...


ParsedIntentType: TypeAlias = ParsedIntent
