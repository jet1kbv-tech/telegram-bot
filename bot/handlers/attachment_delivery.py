"""Authorized Telegram delivery for canonical event attachments."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from bot.services.event_attachment_query import attachment_visible_parent
from bot.services.event_attachments import get_event_attachment

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AttachmentDeliveryResult:
    sent: int
    failed: int


async def deliver_event_attachments(*, bot: Any, chat_id: int, actor_key: str,
                                    attachment_ids: Iterable[str],
                                    load_data: Callable[[], dict[str, Any]]) -> AttachmentDeliveryResult:
    """Re-authorize and deliver ordered canonical attachments from fresh storage."""
    data = load_data()
    ordered_ids = tuple(dict.fromkeys(str(value) for value in attachment_ids if value))
    sent = failed = 0
    for attachment_id in ordered_ids:
        item = get_event_attachment(data, attachment_id)
        if item is None or attachment_visible_parent(data, item, actor_key) is None:
            failed += 1
            continue
        try:
            if item.get("telegram_media_type") == "photo":
                await bot.send_photo(chat_id=chat_id, photo=item["telegram_file_id"])
            elif item.get("telegram_media_type") == "document":
                await bot.send_document(chat_id=chat_id, document=item["telegram_file_id"])
            else:
                failed += 1
                continue
            sent += 1
        except Exception:
            failed += 1
            logger.warning("Context attachment delivery failed attachment_id=%s", attachment_id)
    return AttachmentDeliveryResult(sent, failed)
