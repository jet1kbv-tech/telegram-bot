from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, MutableMapping

from bot.services.nl_intent import IntentKind

KEY = "ai_query_contexts"


@dataclass(frozen=True, slots=True)
class QueryContext:
    token: str
    intent: IntentKind
    arguments: dict[str, Any]
    actor_key: str
    expires_at: datetime


def create_query_context(user_data: MutableMapping[str, Any], *, intent: IntentKind, arguments: dict[str, Any],
                         actor_key: str, now: datetime, ttl_seconds: int) -> QueryContext:
    token = secrets.token_urlsafe(8)
    value = QueryContext(token, intent, dict(arguments), actor_key, now + timedelta(seconds=ttl_seconds))
    contexts = user_data.setdefault(KEY, {})
    contexts[token] = value
    return value


def get_query_context(user_data: MutableMapping[str, Any], token: str, *, actor_key: str, now: datetime) -> QueryContext | None:
    contexts = user_data.get(KEY)
    value = contexts.get(token) if isinstance(contexts, dict) else None
    if not isinstance(value, QueryContext) or value.actor_key != actor_key or value.expires_at <= now:
        if isinstance(contexts, dict) and isinstance(value, QueryContext) and value.expires_at <= now:
            contexts.pop(token, None)
        return None
    return value
