import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot.handlers import nl_event_attachments as handler
from bot.services.nl_attachment_context import KEY, append_file, create_pending, get_pending
from bot.services.nl_dates import zoned_now
from bot.services.nl_entity_resolution import resolve_attachment_events, upcoming_attachment_events


def run(value): return asyncio.run(value)

def data():
    return {"calendars": {"vova": [
        {"id": "c1", "owner": "vova", "title": "Поездка", "source": "manual", "date": "2099-01-01", "start_time": "10:00"},
        {"id": "projection", "owner": "vova", "title": "Концерт", "source": "afisha", "source_id": "a1", "date": "2099-01-02"},
    ], "sasha": [{"id": "secret", "title": "Поездка", "source": "manual", "date": "2099-01-01"}]},
    "afisha": [{"id": "a1", "title": "Концерт", "status": "active", "date": "2099-01-02", "time": "20:00"}],
    "event_attachments": []}


def draft(unique="u1", media="document"):
    return {"telegram_media_type": media, "telegram_file_id": "private-id", "telegram_file_unique_id": unique,
            "file_name": "private.pdf", "mime_type": "application/pdf"}


def test_pending_ttl_and_batch_deduplication():
    now = zoned_now("Europe/Moscow"); user_data = {}
    operation = create_pending(user_data, actor_key="vova", now=now, metadata={}, files=[])
    assert append_file(operation, draft()) and not append_file(operation, draft())
    assert get_pending(user_data, actor_key="vova", now=now) is operation
    assert get_pending(user_data, actor_key="vova", now=now + timedelta(minutes=31)) is None
    assert KEY not in user_data


def test_resolution_owner_isolated_and_projection_not_duplicated():
    assert [(x.bucket, x.item_id) for x in resolve_attachment_events(data(), "Поездка", owner="vova")] == [("calendar", "c1")]
    choices = upcoming_attachment_events(data(), owner="vova", limit=8)
    assert [(x.bucket, x.item_id) for x in choices] == [("calendar", "c1"), ("afisha", "a1")]


def test_orphan_document_does_not_use_provider_and_is_bounded(monkeypatch):
    store = SimpleNamespace(load=lambda: data())
    monkeypatch.setattr(handler, "storage", store); monkeypatch.setattr(handler, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(handler, "get_allowed_profile", lambda update: {"wishlist_owner": "vova"})
    monkeypatch.setattr(handler, "get_username", lambda update: "vova")
    message = SimpleNamespace(document=SimpleNamespace(file_id="id", file_unique_id="unique", file_name="secret.pdf", mime_type="application/pdf"), photo=[], reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message)
    context = SimpleNamespace(user_data={})
    assert run(handler.orphan_attachment_handler(update, context)) == handler.SELECTING_NL_ATTACHMENT_EVENT
    assert message.reply_text.await_args.args[0] == "К какому событию прикрепить этот документ?"
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert len(markup.inline_keyboard) <= 11
    assert "🔎 Указать название" in [button.text for row in markup.inline_keyboard for button in row]


def configure(monkeypatch, store):
    monkeypatch.setattr(handler, "storage", store)
    monkeypatch.setattr(handler, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(handler, "get_allowed_profile", lambda update: {"wishlist_owner": "vova"})
    monkeypatch.setattr(handler, "get_username", lambda update: "wp_bvv")
    monkeypatch.setattr(handler, "get_user_name", lambda update: "Вова")


def callback(value):
    message = SimpleNamespace(reply_text=AsyncMock())
    query = SimpleNamespace(data=value, answer=AsyncMock(), edit_message_text=AsyncMock(), message=message)
    return SimpleNamespace(callback_query=query, effective_message=message,
                           effective_user=SimpleNamespace(username="wp_bvv"))


def test_exact_title_fallback_reaches_event_outside_first_eight(monkeypatch):
    source = data()
    source["calendars"]["vova"] = [
        {"id": f"c{i}", "owner": "vova", "title": f"Событие {i}", "source": "manual",
         "date": f"2099-01-{i + 1:02d}", "start_time": "10:00"} for i in range(9)
    ]
    store = SimpleNamespace(load=lambda: source); configure(monkeypatch, store)
    message = SimpleNamespace(document=SimpleNamespace(file_id="id", file_unique_id="unique",
        file_name="secret.pdf", mime_type="application/pdf"), photo=[], reply_text=AsyncMock())
    context = SimpleNamespace(user_data={}); update = SimpleNamespace(effective_message=message)
    run(handler.orphan_attachment_handler(update, context))
    operation = context.user_data[KEY]
    assert all(candidate["item"]["title"] != "Событие 8" for candidate in operation.candidates)
    title_message = SimpleNamespace(text="Событие 8", reply_text=AsyncMock())
    state = run(handler.attachment_event_title_handler(SimpleNamespace(effective_message=title_message), context))
    assert state == handler.SELECTING_NL_ATTACHMENT_EVENT
    assert operation.parent_id == "c8" and "Что это за документ?" in title_message.reply_text.await_args.args[0]


def test_two_file_confirmation_has_one_save_boundary_and_is_idempotent(monkeypatch):
    source = data(); store = SimpleNamespace(load=Mock(return_value=source), save=Mock()); configure(monkeypatch, store)
    now = zoned_now("Europe/Moscow"); context = SimpleNamespace(user_data={})
    operation = create_pending(context.user_data, actor_key="wp_bvv", now=now,
        metadata={"semantic_type": "transport_ticket", "person": "current_user"},
        files=[draft("one"), draft("two", "photo")])
    operation.parent_type, operation.parent_id, operation.event_title = "calendar", "c1", "Поездка"
    update = callback(f"nla:c:{operation.operation_id}")
    run(handler.nl_attachment_callback_router(update, context))
    store.save.assert_called_once()
    saved = store.save.call_args.args[0]
    assert len(saved["event_attachments"]) == 2
    assert {item["telegram_file_unique_id"] for item in saved["event_attachments"]} == {"one", "two"}
    assert {item["person"] for item in saved["event_attachments"]} == {"vova"}
    run(handler.nl_attachment_callback_router(callback(f"nla:c:{operation.operation_id}"), context))
    store.save.assert_called_once()


def test_existing_event_file_is_deduplicated_with_single_batch_save(monkeypatch):
    source = data()
    from bot.services.event_attachments import create_event_attachment
    create_event_attachment(source, parent_type="calendar", parent_event_id="c1", **draft("existing"))
    store = SimpleNamespace(load=Mock(return_value=source), save=Mock()); configure(monkeypatch, store)
    context = SimpleNamespace(user_data={}); operation = create_pending(context.user_data, actor_key="wp_bvv",
        now=zoned_now("Europe/Moscow"), metadata={"semantic_type": "other"},
        files=[draft("existing"), draft("new")])
    operation.parent_type, operation.parent_id, operation.event_title = "calendar", "c1", "Поездка"
    run(handler.nl_attachment_callback_router(callback(f"nla:c:{operation.operation_id}"), context))
    assert len(store.save.call_args.args[0]["event_attachments"]) == 2
    store.save.assert_called_once()


def test_cancel_clears_every_phase_and_next_file_starts_new_operation(monkeypatch):
    store = SimpleNamespace(load=lambda: data()); configure(monkeypatch, store)
    for stage in ("select_event", "collect", "classify", "transport", "confirm"):
        context = SimpleNamespace(user_data={})
        operation = create_pending(context.user_data, actor_key="wp_bvv", now=zoned_now("Europe/Moscow"),
                                   metadata={}, files=[draft()], stage=stage)
        run(handler.nl_attachment_callback_router(callback(f"nla:x:{operation.operation_id}"), context))
        assert KEY not in context.user_data
    context = SimpleNamespace(user_data={})
    old = create_pending(context.user_data, actor_key="wp_bvv", now=zoned_now("Europe/Moscow"), metadata={})
    context.user_data.clear()  # Generic menu handlers clear all conversation data.
    message = SimpleNamespace(document=SimpleNamespace(file_id="new", file_unique_id="new",
        file_name="", mime_type="application/pdf"), photo=[], reply_text=AsyncMock())
    run(handler.orphan_attachment_handler(SimpleNamespace(effective_message=message), context))
    assert context.user_data[KEY].operation_id != old.operation_id


def test_every_stale_callback_and_next_file_clear_expired_context(monkeypatch):
    store = SimpleNamespace(load=lambda: data()); configure(monkeypatch, store)
    for action in ("d", "e:0", "t:other", "r:train", "c"):
        context = SimpleNamespace(user_data={})
        operation = create_pending(context.user_data, actor_key="wp_bvv",
            now=zoned_now("Europe/Moscow") - timedelta(minutes=31), metadata={}, files=[draft()])
        update = callback(f"nla:{action.split(':')[0]}:{operation.operation_id}" +
                          (":" + action.split(':', 1)[1] if ":" in action else ""))
        run(handler.nl_attachment_callback_router(update, context))
        assert KEY not in context.user_data
        assert update.callback_query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].text == "🏠 В меню"
    context = SimpleNamespace(user_data={})
    create_pending(context.user_data, actor_key="wp_bvv", now=zoned_now("Europe/Moscow") - timedelta(minutes=31), metadata={})
    message = SimpleNamespace(document=SimpleNamespace(file_id="id", file_unique_id="u", file_name="", mime_type=""),
                              photo=[], reply_text=AsyncMock())
    run(handler.collect_attachment_handler(SimpleNamespace(effective_message=message), context))
    assert KEY not in context.user_data


def test_attachment_logs_are_structural_only(monkeypatch, caplog):
    source = data(); store = SimpleNamespace(load=Mock(return_value=source), save=Mock()); configure(monkeypatch, store)
    context = SimpleNamespace(user_data={}); operation = create_pending(context.user_data, actor_key="wp_bvv",
        now=zoned_now("Europe/Moscow"), metadata={"semantic_type": "other", "origin": "SECRET_ORIGIN",
        "destination": "SECRET_DESTINATION", "person": "current_user"}, files=[{
            "telegram_media_type": "document", "telegram_file_id": "SECRET_FILE_ID",
            "telegram_file_unique_id": "SECRET_UNIQUE", "file_name": "SECRET_NAME.pdf",
            "mime_type": "SECRET_MIME",
        }])
    operation.parent_type, operation.parent_id, operation.event_title = "calendar", "c1", "Поездка"
    with caplog.at_level(logging.INFO):
        run(handler.nl_attachment_callback_router(callback(f"nla:c:{operation.operation_id}"), context))
    for secret in ("SECRET_FILE_ID", "SECRET_UNIQUE", "SECRET_NAME", "SECRET_MIME",
                   "SECRET_ORIGIN", "SECRET_DESTINATION", "current_user"):
        assert secret not in caplog.text
