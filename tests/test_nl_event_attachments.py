import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot.handlers import common
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


def production_data(*, upcoming=True):
    return {"calendars": {"vova": []}, "afisha": [{
        "id": "sanatorium", "title": "Санаторий", "status": "active",
        "date": "2099-06-01" if upcoming else "2000-06-01",
    }], "event_attachments": []}


def intent_update(*, with_file=False):
    document = (SimpleNamespace(file_id="file-id", file_unique_id="file-unique",
                                file_name="voucher.pdf", mime_type="application/pdf")
                if with_file else None)
    message = SimpleNamespace(document=document, photo=[], reply_text=AsyncMock())
    return SimpleNamespace(effective_message=message), message


def test_production_reference_uses_user_chooser_without_fuzzy_match(monkeypatch):
    source = production_data(); store = SimpleNamespace(load=lambda: source); configure(monkeypatch, store)
    context = SimpleNamespace(user_data={}); update, response = intent_update()

    state = run(handler.begin_intent_attachment(update, context, {
        "target": "поездка в санаторий", "semantic_type": "voucher",
    }, response))

    assert resolve_attachment_events(source, "поездка в санаторий", owner="vova") == []
    assert state == handler.SELECTING_NL_ATTACHMENT_EVENT
    operation = context.user_data[KEY]
    assert operation.metadata == {"semantic_type": "voucher"}
    assert [candidate["item"]["title"] for candidate in operation.candidates] == ["Санаторий"]
    assert response.reply_text.await_args.args[0].startswith("Не нашёл точного совпадения")


def test_file_and_metadata_survive_fallback_then_one_attachment_is_saved(monkeypatch):
    source = production_data(); store = SimpleNamespace(load=Mock(return_value=source), save=Mock())
    configure(monkeypatch, store)
    context = SimpleNamespace(user_data={}); update, response = intent_update(with_file=True)
    run(handler.begin_intent_attachment(update, context, {
        "target": "поездка в санаторий", "semantic_type": "voucher",
    }, response))
    operation = context.user_data[KEY]
    assert len(operation.files) == 1
    assert operation.files[0]["telegram_file_unique_id"] == "file-unique"
    assert operation.files[0]["telegram_file_id"] == "file-id"

    selected = callback(f"nla:e:{operation.operation_id}:0")
    assert run(handler.nl_attachment_callback_router(selected, context)) == handler.CONFIRMING_NL_ATTACHMENT
    assert "Тип: 🏨 Ваучер / проживание" in selected.callback_query.message.reply_text.await_args.args[0]
    assert run(handler.nl_attachment_callback_router(
        callback(f"nla:c:{operation.operation_id}"), context,
    )) == handler._idle(context)
    store.save.assert_called_once()
    assert len(store.save.call_args.args[0]["event_attachments"]) == 1


def test_metadata_survives_fallback_and_later_file_needs_no_provider(monkeypatch):
    source = production_data(); store = SimpleNamespace(load=lambda: source); configure(monkeypatch, store)
    context = SimpleNamespace(user_data={}); update, response = intent_update()
    run(handler.begin_intent_attachment(update, context, {
        "target": "поездка в санаторий", "semantic_type": "transport_ticket", "destination": "Воронеж",
    }, response))
    operation = context.user_data[KEY]

    assert run(handler.nl_attachment_callback_router(
        callback(f"nla:e:{operation.operation_id}:0"), context,
    )) == handler.WAITING_FOR_NL_ATTACHMENTS
    file_update, _ = intent_update(with_file=True)
    assert run(handler.collect_attachment_handler(file_update, context)) == handler.WAITING_FOR_NL_ATTACHMENTS
    assert operation.metadata == {"semantic_type": "transport_ticket", "destination": "Воронеж"}
    assert len(operation.files) == 1


def test_exact_match_canonicalizes_date_and_time_for_every_batch_file(monkeypatch):
    source = production_data(); store = SimpleNamespace(load=Mock(return_value=source), save=Mock())
    configure(monkeypatch, store); context = SimpleNamespace(user_data={}); update, response = intent_update(with_file=True)
    state = run(handler.begin_intent_attachment(update, context, {
        "target": "санаторий", "semantic_type": "transport_ticket", "transport_type": "train",
        "origin": "Москва", "destination": "Воронеж", "date_expression": "31 августа",
        "departure_time": "08:10",
    }, response))
    operation = context.user_data[KEY]; operation.files.append(draft("second"))
    assert state == handler.SELECTING_NL_ATTACHMENT_EVENT
    state = run(handler.nl_attachment_callback_router(callback(f"nla:s:{operation.operation_id}"), context))
    assert state == handler.CONFIRMING_NL_ATTACHMENT
    assert operation.metadata["date"] == "2026-08-31" and "date_expression" not in operation.metadata
    run(handler.nl_attachment_callback_router(callback(f"nla:c:{operation.operation_id}"), context))
    saved = store.save.call_args.args[0]["event_attachments"]
    assert len(saved) == 2
    assert {(item["date"], item["departure_time"], item["origin"], item["destination"]) for item in saved} == {
        ("2026-08-31", "08:10", "Москва", "Воронеж")}
    assert all("date_expression" not in item for item in saved)


def test_zero_candidate_fallback_keeps_canonical_date_and_drops_unparseable_expression(monkeypatch):
    source = production_data(); store = SimpleNamespace(load=Mock(return_value=source), save=Mock())
    configure(monkeypatch, store); context = SimpleNamespace(user_data={}); update, response = intent_update(with_file=True)
    run(handler.begin_intent_attachment(update, context, {
        "target": "поездка в санаторий", "semantic_type": "transport_ticket",
        "date_expression": "завтра", "departure_time": "08:10",
    }, response))
    operation = context.user_data[KEY]
    assert operation.metadata["date"] and "date_expression" not in operation.metadata
    run(handler.nl_attachment_callback_router(callback(f"nla:e:{operation.operation_id}:0"), context))
    run(handler.nl_attachment_callback_router(callback(f"nla:c:{operation.operation_id}"), context))
    assert store.save.call_args.args[0]["event_attachments"][0]["date"] == operation.metadata["date"]

    context = SimpleNamespace(user_data={}); update, response = intent_update()
    run(handler.begin_intent_attachment(update, context, {
        "target": "санаторий", "semantic_type": "transport_ticket", "date_expression": "когда-нибудь",
    }, response))
    assert "date" not in context.user_data[KEY].metadata and "date_expression" not in context.user_data[KEY].metadata


def test_zero_match_and_no_upcoming_events_enters_exact_title_without_losing_operation(monkeypatch):
    source = production_data(upcoming=False); store = SimpleNamespace(load=lambda: source); configure(monkeypatch, store)
    context = SimpleNamespace(user_data={}); update, response = intent_update(with_file=True)
    state = run(handler.begin_intent_attachment(update, context, {
        "target": "поездка в санаторий", "semantic_type": "voucher",
    }, response))
    operation = context.user_data[KEY]
    assert state == handler.ENTERING_NL_ATTACHMENT_EVENT_TITLE
    assert operation.stage == "enter_title" and operation.files and operation.metadata["semantic_type"] == "voucher"
    assert "точное название" in response.reply_text.await_args.args[0]


def test_exact_and_ambiguous_matches_keep_existing_paths(monkeypatch):
    source = production_data(); store = SimpleNamespace(load=lambda: source); configure(monkeypatch, store)
    context = SimpleNamespace(user_data={}); update, response = intent_update()
    assert run(handler.begin_intent_attachment(update, context, {
        "target": "санаторий", "semantic_type": "voucher",
    }, response)) == handler.WAITING_FOR_NL_ATTACHMENTS
    assert context.user_data[KEY].parent_id == "sanatorium"

    source["afisha"].append({**source["afisha"][0], "id": "sanatorium-2"})
    context = SimpleNamespace(user_data={}); update, response = intent_update()
    assert run(handler.begin_intent_attachment(update, context, {
        "target": "Санаторий", "semantic_type": "voucher",
    }, response)) == handler.SELECTING_NL_ATTACHMENT_EVENT
    assert len(context.user_data[KEY].candidates) == 2
    assert response.reply_text.await_args.args[0] == "Нашёл несколько похожих событий. Какое выбрать?"


def test_cancel_from_zero_candidate_chooser_clears_without_mutation(monkeypatch):
    source = production_data(); store = SimpleNamespace(load=lambda: source, save=Mock()); configure(monkeypatch, store)
    context = SimpleNamespace(user_data={}); update, response = intent_update(with_file=True)
    run(handler.begin_intent_attachment(update, context, {
        "target": "поездка в санаторий", "semantic_type": "voucher",
    }, response))
    operation = context.user_data[KEY]
    run(handler.nl_attachment_callback_router(callback(f"nla:x:{operation.operation_id}"), context))
    assert KEY not in context.user_data
    store.save.assert_not_called()


def test_main_menu_from_zero_candidate_chooser_clears_pending_context(monkeypatch):
    source = production_data(); store = SimpleNamespace(load=lambda: source); configure(monkeypatch, store)
    context = SimpleNamespace(user_data={}); update, response = intent_update()
    run(handler.begin_intent_attachment(update, context, {
        "target": "поездка в санаторий", "semantic_type": "voucher",
    }, response))
    assert KEY in context.user_data

    monkeypatch.setattr(common, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(common, "remember_current_chat", AsyncMock())
    common.configure_common_handlers(main_menu_keyboard=lambda: "main-keyboard", safe_edit_message=AsyncMock())
    menu_update = callback("menu:main")
    menu_update.message = None
    assert run(common.back_to_main(menu_update, context)) == handler._idle(SimpleNamespace(user_data={}))
    assert context.user_data == {}


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
