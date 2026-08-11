from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from secrets import token_urlsafe
from typing import Any

KEY = "nl_attachment_operation"
TTL_SECONDS = 30 * 60


@dataclass(slots=True)
class PendingAttachmentOperation:
    operation_id: str
    actor_key: str
    created_at: datetime
    expires_at: datetime
    stage: str
    metadata: dict[str, Any] = field(default_factory=dict)
    files: list[dict[str, str]] = field(default_factory=list)
    parent_type: str | None = None
    parent_id: str | None = None
    event_title: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    completed: bool = False


def create_pending(user_data: dict[str, Any], *, actor_key: str, now: datetime,
                   metadata: dict[str, Any], files: list[dict[str, str]] | None = None,
                   stage: str = "select_event") -> PendingAttachmentOperation:
    operation = PendingAttachmentOperation(token_urlsafe(8), actor_key, now,
        now + timedelta(seconds=TTL_SECONDS), stage, dict(metadata), list(files or []))
    user_data[KEY] = operation
    return operation


def get_pending(user_data: dict[str, Any], *, actor_key: str, now: datetime,
                operation_id: str | None = None) -> PendingAttachmentOperation | None:
    operation = user_data.get(KEY)
    if not isinstance(operation, PendingAttachmentOperation) or operation.actor_key != actor_key \
            or operation.expires_at <= now or (operation_id and operation.operation_id != operation_id):
        user_data.pop(KEY, None)
        return None
    return operation


def clear_pending(user_data: dict[str, Any]) -> None:
    user_data.pop(KEY, None)


def append_file(operation: PendingAttachmentOperation, draft: dict[str, str]) -> bool:
    unique = draft.get("telegram_file_unique_id")
    if unique and any(item.get("telegram_file_unique_id") == unique for item in operation.files):
        return False
    operation.files.append(dict(draft))
    return True
