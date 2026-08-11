import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers import tickets
from bot.states import ADDING_TICKET_ATTACHMENTS


def test_legacy_ticket_document_and_photo_still_use_ticket_draft(monkeypatch):
    monkeypatch.setattr(tickets, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(tickets, "remember_current_chat", AsyncMock())
    context = SimpleNamespace(user_data={})
    for media in ("document", "photo"):
        file = SimpleNamespace(file_id=f"{media}-id", file_name="ticket.pdf", mime_type="application/pdf")
        message = SimpleNamespace(document=file if media == "document" else None,
                                  photo=[] if media == "document" else [file], reply_text=AsyncMock())
        update = SimpleNamespace(message=message)
        assert asyncio.run(tickets.add_ticket_attachment(update, context)) == ADDING_TICKET_ATTACHMENTS
    attachments = context.user_data["ticket_draft"]["attachments"]
    assert [item["kind"] for item in attachments] == ["document", "photo"]
    assert len(attachments) == 2
