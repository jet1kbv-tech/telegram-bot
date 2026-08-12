from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from secrets import token_urlsafe
from typing import Any

KEY = "nl_attachment_query_operation"


@dataclass(slots=True)
class PendingAttachmentQuery:
    operation_id: str
    actor_key: str
    expires_at: datetime
    query: dict[str, Any]
    parent_type: str | None = None
    parent_id: str | None = None
    candidates: list[dict[str, str]] = field(default_factory=list)


def create_pending_query(user_data: dict[str, Any], *, actor_key: str, now: datetime,
                         query: dict[str, Any]) -> PendingAttachmentQuery:
    operation = PendingAttachmentQuery(token_urlsafe(8), actor_key, now + timedelta(minutes=30), dict(query))
    user_data[KEY] = operation
    return operation


def get_pending_query(user_data: dict[str, Any], *, actor_key: str, now: datetime,
                      operation_id: str) -> PendingAttachmentQuery | None:
    value = user_data.get(KEY)
    if not isinstance(value, PendingAttachmentQuery) or value.actor_key != actor_key \
            or value.expires_at <= now or value.operation_id != operation_id:
        user_data.pop(KEY, None); return None
    return value


def clear_pending_query(user_data: dict[str, Any]) -> None:
    user_data.pop(KEY, None)
