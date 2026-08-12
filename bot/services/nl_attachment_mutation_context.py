from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from secrets import token_urlsafe
from typing import Any

KEY = "nl_attachment_mutation_operation"


@dataclass(slots=True)
class PendingAttachmentMutation:
    operation_id: str
    actor_key: str
    intent: str
    expires_at: datetime
    query: dict[str, Any]
    changes: dict[str, str] = field(default_factory=dict)
    parent_type: str | None = None
    parent_id: str | None = None
    candidates: list[str] = field(default_factory=list)
    selected_id: str | None = None


def create_pending_mutation(user_data: dict[str, Any], *, actor_key: str, intent: str,
                            now: datetime, query: dict[str, Any], changes: dict[str, str],
                            ttl_seconds: int = 900) -> PendingAttachmentMutation:
    operation = PendingAttachmentMutation(token_urlsafe(8), actor_key, intent,
        now + timedelta(seconds=ttl_seconds), dict(query), dict(changes))
    user_data[KEY] = operation
    return operation


def get_pending_mutation(user_data: dict[str, Any], *, actor_key: str, now: datetime,
                         operation_id: str) -> PendingAttachmentMutation | None:
    operation = user_data.get(KEY)
    if not isinstance(operation, PendingAttachmentMutation) or operation.actor_key != actor_key \
            or operation.operation_id != operation_id or operation.expires_at <= now:
        user_data.pop(KEY, None)
        return None
    return operation


def clear_pending_mutation(user_data: dict[str, Any]) -> None:
    user_data.pop(KEY, None)
