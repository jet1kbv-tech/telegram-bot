import asyncio
from unittest.mock import AsyncMock

from bot.handlers.attachment_delivery import deliver_event_attachments


def run(coro):
    return asyncio.run(coro)


def snapshot(*attachments):
    return {
        "afisha": [{"id": "a", "status": "active"}],
        "calendars": {
            "vova": [{"id": "private", "source": "manual"},
                     {"id": "projection", "source": "afisha", "source_id": "a"}],
            "sasha": [{"id": "secret", "source": "manual"}],
        },
        "event_attachments": list(attachments),
    }


def attachment(identity, *, parent_type="afisha", parent_id="a", media="document", file_id=None):
    return {
        "id": identity, "parent_type": parent_type, "parent_event_id": parent_id,
        "telegram_media_type": media, "telegram_file_id": file_id or f"file-{identity}",
    }


def test_delivers_documents_and_photos_in_order_from_one_fresh_snapshot():
    data = snapshot(attachment("document"), attachment("photo", media="photo"))
    loads = 0

    def load_data():
        nonlocal loads
        loads += 1
        return data

    events = []
    bot = AsyncMock()
    bot.send_document.side_effect = lambda **kwargs: events.append(("document", kwargs["document"]))
    bot.send_photo.side_effect = lambda **kwargs: events.append(("photo", kwargs["photo"]))

    result = run(deliver_event_attachments(
        bot=bot, chat_id=1, actor_key="vova",
        attachment_ids=("document", "photo"), load_data=load_data))

    assert loads == 1
    assert events == [("document", "file-document"), ("photo", "file-photo")]
    assert (result.sent, result.failed) == (2, 0)


def test_failure_and_disappearance_do_not_abort_later_files():
    data = snapshot(attachment("bad"), attachment("good"))
    bot = AsyncMock()
    bot.send_document.side_effect = [RuntimeError("stale file id"), None]

    result = run(deliver_event_attachments(
        bot=bot, chat_id=1, actor_key="vova",
        attachment_ids=("bad", "deleted", "good"), load_data=lambda: data))

    assert [call.kwargs["document"] for call in bot.send_document.await_args_list] == [
        "file-bad", "file-good"]
    assert (result.sent, result.failed) == (1, 2)


def test_private_other_actor_is_denied_and_duplicate_ids_send_once():
    private = attachment("private", parent_type="calendar", parent_id="secret")
    canonical = attachment("canonical")
    data = snapshot(private, canonical)
    bot = AsyncMock()

    result = run(deliver_event_attachments(
        bot=bot, chat_id=1, actor_key="vova",
        attachment_ids=("private", "canonical", "canonical"), load_data=lambda: data))

    bot.send_document.assert_awaited_once_with(chat_id=1, document="file-canonical")
    assert (result.sent, result.failed) == (1, 1)


def test_afisha_projection_resolves_to_canonical_attachment_once():
    data = snapshot(attachment("canonical"))
    bot = AsyncMock()
    result = run(deliver_event_attachments(
        bot=bot, chat_id=1, actor_key="vova",
        attachment_ids=("canonical", "canonical"), load_data=lambda: data))
    bot.send_document.assert_awaited_once_with(chat_id=1, document="file-canonical")
    assert (result.sent, result.failed) == (1, 0)
