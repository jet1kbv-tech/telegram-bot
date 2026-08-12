import json
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers.calendar import calendar_event_keyboard, calendar_event_readonly_keyboard
from bot.handlers import event_attachments as handlers
from bot.handlers import calendar as calendar_handlers
from bot.handlers.afisha import apply_afisha_delete, apply_afisha_status_update
from bot import runtime
from bot.keyboards.common import item_keyboard, main_menu_keyboard, section_menu_keyboard
from bot.services.event_attachments import (
    AttachmentParentNotFound,
    create_event_attachment,
    delete_attachments_for_event,
    delete_event_attachment,
    get_attachments_for_event,
    get_event_attachment,
    list_event_attachments,
    update_event_attachment_metadata,
)
from bot.services.event_attachment_display import attachment_detail_text, attachment_list_title, attachment_list_titles
from bot.storage import JsonStorage
from telegram.error import BadRequest


def _data():
    calendar_fields = {"date": "2026-08-20", "start_time": "10:00", "end_time": "", "comment": "", "notified_24h": False}
    afisha_fields = {"date": "2026-08-20", "time": "10:00", "end_date": "", "end_time": "", "place": "", "link": "", "status": "active", "notified_24h": False, "notified_morning": False}
    return {
        "afisha": [{"id": "afi1", "title": "Trip", **afisha_fields}, {"id": "afi2", "title": "Other", **afisha_fields}],
        "calendars": {
            "vova": [{"id": "cal1", "owner": "vova", "title": "Personal", "source": "manual", "source_id": "", **calendar_fields},
                     {"id": "projection", "owner": "vova", "title": "Trip", "source": "afisha", "source_id": "afi1", **calendar_fields}],
            "sasha": [{"id": "cal2", "owner": "sasha", "title": "Other", "source": "manual", "source_id": "", **calendar_fields}],
        },
        "event_attachments": [],
    }


def _create(data, parent_type="calendar", parent_id="cal1", unique="unique"):
    return create_event_attachment(data, parent_type=parent_type, parent_event_id=parent_id,
        telegram_file_id="secret-file-id", telegram_file_unique_id=unique,
        telegram_media_type="document", file_name="ticket.pdf", mime_type="application/pdf")


def test_legacy_storage_without_attachment_key_loads(tmp_path):
    path = tmp_path / "data.json"
    path.write_text(json.dumps({"tickets": {"active": [], "used": []}}), encoding="utf-8")
    assert JsonStorage(path).load()["event_attachments"] == []


def test_create_get_list_delete_and_parent_safety():
    data = _data(); item, created = _create(data)
    assert created and get_event_attachment(data, item["id"]) == item
    assert list_event_attachments(data) == [item]
    assert get_attachments_for_event(data, "calendar", "cal1") == [item]
    assert delete_event_attachment(data, item["id"])
    assert data["calendars"]["vova"][0]["id"] == "cal1"


def test_duplicate_is_idempotent_on_same_event_but_allowed_on_another():
    data = _data(); first, created = _create(data)
    duplicate, created_again = _create(data)
    other, other_created = _create(data, parent_id="cal2")
    assert created and not created_again and duplicate is first
    assert other_created and other["id"] != first["id"] and len(data["event_attachments"]) == 2


def test_afisha_projection_resolves_to_source_without_copy():
    data = _data(); item, _ = _create(data, "calendar", "projection")
    assert item["parent_type"] == "afisha" and item["parent_event_id"] == "afi1"
    assert get_attachments_for_event(data, "afisha", "afi1") == [item]
    assert get_attachments_for_event(data, "calendar", "projection") == [item]
    assert len(data["event_attachments"]) == 1


def test_invalid_parent_rejected():
    with pytest.raises(AttachmentParentNotFound):
        _create(_data(), parent_id="missing")


def test_event_delete_cascade_only_removes_its_attachments():
    data = _data(); _create(data); _create(data, parent_id="cal2")
    assert delete_attachments_for_event(data, "calendar", "cal1") == 1
    assert len(data["event_attachments"]) == 1


def _labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def test_documents_button_visible_on_event_cards_and_projection():
    assert "📎 Документы" in _labels(calendar_event_keyboard("vova", "cal1", 0))
    assert "📎 Документы" in _labels(calendar_event_readonly_keyboard("vova", "afi1", 0))
    afisha = {"id": "afi1", "status": "active"}
    assert "📎 Документы" in _labels(item_keyboard("afisha", afisha, 0))
    assert "🎟 Билеты" not in _labels(item_keyboard("afisha", afisha, 0))
    assert not any(button.callback_data.startswith("tickets:") for row in item_keyboard("afisha", afisha, 0).inline_keyboard for button in row)


def test_normal_current_menus_and_event_cards_expose_no_legacy_ticket_route():
    markups = [main_menu_keyboard(), *(section_menu_keyboard(section) for section in
        ("films", "wishlist", "afisha", "backlog", "leisure")),
        calendar_event_keyboard("vova", "cal1", 0), calendar_event_readonly_keyboard("vova", "afi1", 0)]
    for markup in markups:
        buttons = [button for row in markup.inline_keyboard for button in row]
        assert all(button.text not in {"🎟 Билеты", "➕ Добавить билет"} for button in buttons)
        assert all(not (button.callback_data or "").startswith("tickets:") for button in buttons)


def test_transport_display_titles_details_and_collisions():
    train = {"semantic_type": "transport_ticket", "transport_type": "train", "origin": "Москва",
             "destination": "Воронеж", "date": "2026-08-31", "departure_time": "08:10", "person": "both"}
    assert attachment_list_title(train) == "🚆 Москва → Воронеж · 31.08"
    assert attachment_list_title({**train, "transport_type": "plane", "destination": "Стамбул"}) == "✈️ Москва → Стамбул · 31.08"
    assert attachment_list_title({**train, "date": None}) == "🚆 Москва → Воронеж"
    assert attachment_list_title({**train, "origin": None, "date": None}) == "🚆 Билет · Воронеж"
    fallback = {**train, "origin": None, "destination": None, "date": None}
    assert attachment_list_title(fallback) == "🚆 Билет на поезд"
    assert attachment_list_titles([fallback, fallback]) == ["🚆 Билет на поезд · #1", "🚆 Билет на поезд · #2"]
    assert "08:10" not in attachment_list_title(train)
    detail = attachment_detail_text(train)
    assert "Москва → Воронеж" in detail and "31 августа 2026" in detail
    assert "Отправление: 08:10" in detail and "Для: Вова и Саша" in detail
    other = {"semantic_type": "insurance", "origin": "Секрет", "destination": "Секрет",
             "date": "2026-08-31", "departure_time": "08:10", "person": "both"}
    assert attachment_detail_text(other) == "🛡 Страховка"


def test_three_collisions_and_mixed_titles_are_stable():
    fallback = {"semantic_type": "transport_ticket", "transport_type": "train"}
    voucher = {"semantic_type": "voucher"}
    assert attachment_list_titles([fallback, voucher, fallback, fallback]) == [
        "🚆 Билет на поезд · #1", "🏨 Ваучер / проживание",
        "🚆 Билет на поезд · #2", "🚆 Билет на поезд · #3",
    ]


def test_metadata_update_is_allowlisted_validated_and_preserves_identity():
    data = _data(); item, _ = _create(data); identity = {key: item[key] for key in
        ("id", "parent_type", "parent_event_id", "telegram_file_id", "telegram_file_unique_id")}
    update_event_attachment_metadata(data, item["id"], origin=" Москва ", destination="Воронеж",
                                     date="2026-08-31", departure_time="08:10", person="both")
    assert item["origin"] == "Москва" and item["destination"] == "Воронеж"
    assert {key: item[key] for key in identity} == identity
    update_event_attachment_metadata(data, item["id"], origin=None)
    assert item["origin"] is None
    with pytest.raises(ValueError): update_event_attachment_metadata(data, item["id"], date="31 августа")
    with pytest.raises(ValueError): update_event_attachment_metadata(data, item["id"], telegram_file_id="changed")
    with pytest.raises(ValueError): update_event_attachment_metadata(data, "missing", origin="Москва")


def test_one_field_edit_and_clear_preserve_all_other_metadata():
    data = _data(); item, _ = _create(data)
    update_event_attachment_metadata(data, item["id"], origin="Москва", destination="Воронеж",
                                     date="2026-08-31", departure_time="08:10", person="both")
    update_event_attachment_metadata(data, item["id"], destination="Стамбул")
    assert (item["origin"], item["destination"], item["date"], item["departure_time"], item["person"]) == (
        "Москва", "Стамбул", "2026-08-31", "08:10", "both")
    update_event_attachment_metadata(data, item["id"], origin=None)
    assert (item["origin"], item["destination"], item["date"], item["departure_time"], item["person"]) == (
        None, "Стамбул", "2026-08-31", "08:10", "both")


def test_attachment_normalization_and_legacy_tickets_roundtrip(tmp_path):
    store = JsonStorage(tmp_path / "data.json"); data = _data()
    legacy = {"id": "old", "title": "Старый билет", "date": "2020-01-01", "time": "10:00",
              "place_route": "Москва", "comment": "", "attachments": [{"kind": "document", "file_id": "legacy"}]}
    data["tickets"] = {"active": [legacy], "used": []}
    item, _ = _create(data); item.update({"date": "bad", "departure_time": "25:99"})
    store.save(data); loaded = store.load()
    assert loaded["event_attachments"][0]["date"] is None and loaded["event_attachments"][0]["departure_time"] is None
    assert loaded["tickets"]["active"][0]["title"] == "Старый билет"


def test_complete_legacy_ticket_structure_survives_roundtrip_without_migration(tmp_path):
    store = JsonStorage(tmp_path / "data.json")
    active = {"id": "active-id", "title": "Поезд", "date": "2026-08-31", "time": "08:10",
              "place_route": "Москва → Воронеж", "comment": "верхняя полка", "afisha_id": "afi1",
              "attachments": [{"kind": "document", "file_id": "doc", "file_name": "ticket.pdf",
                               "mime_type": "application/pdf"}]}
    used = {"id": "used-id", "title": "Автобус", "date": "2025-05-01", "time": "09:00",
            "place_route": "A → B", "comment": "архив", "afisha_id": "",
            "attachments": [{"kind": "photo", "file_id": "photo", "file_name": "", "mime_type": ""}]}
    store.path.write_text(json.dumps({"tickets": {"active": [active], "used": [used]}}), encoding="utf-8")
    loaded = store.load(); store.save(loaded); reloaded = store.load()
    assert reloaded["tickets"] == {"active": [active], "used": [used]}
    assert reloaded["event_attachments"] == []


@pytest.mark.parametrize(("date_value", "time_value", "expected_date", "expected_time"), [
    ("2024-02-29", "00:00", "2024-02-29", "00:00"),
    ("2023-02-29", "24:00", None, None),
    ("2026-08-31 ", " 08:10", "2026-08-31", "08:10"),
])
def test_attachment_metadata_normalization(date_value, time_value, expected_date, expected_time):
    data = _data(); item, _ = create_event_attachment(data, parent_type="calendar", parent_event_id="cal1",
        telegram_file_id="file", telegram_file_unique_id="unique", telegram_media_type="document",
        origin=" Москва ", destination="  ", date=date_value, departure_time=time_value, person="invalid")
    assert (item["origin"], item["destination"], item["date"], item["departure_time"], item["person"]) == (
        "Москва", None, expected_date, expected_time, None)


def _run(coro):
    return asyncio.run(coro)


def _context(**user_data):
    return SimpleNamespace(user_data=user_data, bot=SimpleNamespace(send_document=AsyncMock(), send_photo=AsyncMock()))


def _callback(data):
    message = SimpleNamespace(chat_id=7, reply_text=AsyncMock())
    return SimpleNamespace(callback_query=SimpleNamespace(data=data, answer=AsyncMock(), message=message),
        effective_user=SimpleNamespace(username="wp_bvv"), effective_chat=SimpleNamespace(id=7))


def test_empty_documents_screen(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); data = _data(); store.save(data)
    edit = AsyncMock(); monkeypatch.setattr(handlers, "storage", store); handlers.configure_event_attachment_handlers(safe_edit_message=edit)
    ctx = _context()
    _run(handlers.show_documents(_callback("att|cal|vova|cal1|0"), ctx, "calendar", "cal1", "cal_view|vova|cal1|0"))
    assert "Пока документов нет" in edit.await_args.args[1]
    assert "➕ Добавить" in _labels(edit.await_args.kwargs["reply_markup"])


@pytest.mark.parametrize("media", ["document", "photo"])
def test_receive_file_preserves_telegram_metadata(monkeypatch, media):
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    document = SimpleNamespace(file_id="doc-id", file_unique_id="doc-unique", file_name="ticket.pdf", mime_type="application/pdf")
    photo = SimpleNamespace(file_id="photo-id", file_unique_id="photo-unique")
    message = SimpleNamespace(document=document if media == "document" else None,
        photo=[] if media == "document" else [photo], reply_text=AsyncMock())
    ctx = _context(); state = _run(handlers.receive_file(SimpleNamespace(message=message), ctx))
    draft = ctx.user_data["event_attachment_draft"]
    assert state == handlers.SELECTING_EVENT_ATTACHMENT_TYPE
    assert draft["telegram_file_id"] == ("doc-id" if media == "document" else "photo-id")
    assert draft["telegram_file_unique_id"] == ("doc-unique" if media == "document" else "photo-unique")
    assert (draft["file_name"], draft["mime_type"]) == (("ticket.pdf", "application/pdf") if media == "document" else ("", "image/jpeg"))


def test_semantic_transport_selection_and_duplicate_callback_are_idempotent(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); store.save(_data()); monkeypatch.setattr(handlers, "storage", store)
    edit = AsyncMock(); handlers.configure_event_attachment_handlers(safe_edit_message=edit)
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    ctx = _context(event_attachment_parent=("calendar", "cal1"), event_attachment_back="cal_view|vova|cal1|0",
        event_attachment_draft={"telegram_media_type": "document", "telegram_file_id": "id", "telegram_file_unique_id": "unique"})
    assert _run(handlers.event_attachment_router(_callback("att|type|transport_ticket"), ctx)) == handlers.SELECTING_EVENT_ATTACHMENT_TRANSPORT
    _run(handlers.event_attachment_router(_callback("att|transport|train"), ctx))
    _run(handlers.event_attachment_router(_callback("att|skipenrich"), ctx))
    saved = store.load()["event_attachments"]
    assert len(saved) == 1 and saved[0]["semantic_type"] == "transport_ticket" and saved[0]["transport_type"] == "train"
    _run(handlers.event_attachment_router(_callback("att|transport|train"), ctx))
    _run(handlers.event_attachment_router(_callback("att|skipenrich"), ctx))
    assert len(store.load()["event_attachments"]) == 1


def test_native_optional_enrichment_stores_route_date_and_time(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); store.save(_data()); monkeypatch.setattr(handlers, "storage", store)
    handlers.configure_event_attachment_handlers(safe_edit_message=AsyncMock())
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    ctx = _context(event_attachment_parent=("calendar", "cal1"),
        event_attachment_draft={"telegram_media_type": "document", "telegram_file_id": "id", "telegram_file_unique_id": "enriched"})
    _run(handlers.event_attachment_router(_callback("att|transport|train"), ctx))
    assert _run(handlers.event_attachment_router(_callback("att|enrich"), ctx)) == handlers.ENRICHING_EVENT_ATTACHMENT
    for value in ("Москва", "Воронеж", "2026-08-31", "08:10"):
        message = SimpleNamespace(text=value, reply_text=AsyncMock())
        _run(handlers.receive_attachment_metadata(SimpleNamespace(message=message), ctx))
    saved = store.load()["event_attachments"][0]
    assert (saved["origin"], saved["destination"], saved["date"], saved["departure_time"]) == (
        "Москва", "Воронеж", "2026-08-31", "08:10")


def test_native_enrichment_has_cancel_and_menu_escape_and_invalid_input_keeps_draft(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); store.save(_data()); monkeypatch.setattr(handlers, "storage", store)
    edit = AsyncMock(); handlers.configure_event_attachment_handlers(safe_edit_message=edit)
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    draft = {"telegram_media_type": "document", "telegram_file_id": "id", "telegram_file_unique_id": "safe"}
    ctx = _context(event_attachment_parent=("calendar", "cal1"), event_attachment_draft=draft.copy())
    _run(handlers.event_attachment_router(_callback("att|transport|train"), ctx))
    assert {"❌ Отменить", "🏠 В меню"} <= set(_labels(edit.await_args.kwargs["reply_markup"]))
    _run(handlers.event_attachment_router(_callback("att|enrich"), ctx))
    for value in ("Москва", "Воронеж"):
        _run(handlers.receive_attachment_metadata(SimpleNamespace(message=SimpleNamespace(text=value, reply_text=AsyncMock())), ctx))
    invalid = SimpleNamespace(text="31 августа", reply_text=AsyncMock())
    assert _run(handlers.receive_attachment_metadata(SimpleNamespace(message=invalid), ctx)) == handlers.ENRICHING_EVENT_ATTACHMENT
    assert ctx.user_data["event_attachment_draft"] == draft and store.load()["event_attachments"] == []


def test_projection_and_source_share_enriched_attachment_and_edit(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); data = _data()
    item, _ = _create(data, "afisha", "afi1"); store.save(data); monkeypatch.setattr(handlers, "storage", store)
    assert get_attachments_for_event(store.load(), "calendar", "projection")[0]["id"] == item["id"]
    canonical = store.load(); projection_item = get_attachments_for_event(canonical, "calendar", "projection")[0]
    update_event_attachment_metadata(canonical, projection_item["id"], destination="Воронеж"); store.save(canonical)
    assert get_attachments_for_event(store.load(), "afisha", "afi1")[0]["destination"] == "Воронеж"
    assert len(store.load()["event_attachments"]) == 1


def test_detail_keeps_actions_and_adds_edit(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); data = _data(); item, _ = _create(data); store.save(data)
    edit = AsyncMock(); monkeypatch.setattr(handlers, "storage", store)
    handlers.configure_event_attachment_handlers(safe_edit_message=edit)
    _run(handlers.show_detail(_callback(f"att|detail|{item['id']}"), _context(), item["id"]))
    assert {"📤 Отправить файл", "✏️ Изменить данные", "🗑 Удалить", "⬅️ Назад"} <= set(
        _labels(edit.await_args.kwargs["reply_markup"]))


@pytest.mark.parametrize(("media", "method"), [("document", "send_document"), ("photo", "send_photo")])
def test_attachment_delivery_uses_matching_telegram_method(monkeypatch, tmp_path, media, method):
    store = JsonStorage(tmp_path / "data.json"); data = _data()
    item, _ = create_event_attachment(data, parent_type="calendar", parent_event_id="cal1", telegram_file_id="private",
        telegram_file_unique_id="unique", telegram_media_type=media); store.save(data); monkeypatch.setattr(handlers, "storage", store)
    handlers.configure_event_attachment_handlers(safe_edit_message=AsyncMock())
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    ctx = _context(event_attachment_parent=("calendar", "cal1")); _run(handlers.event_attachment_router(_callback(f"att|send|{item['id']}"), ctx))
    getattr(ctx.bot, method).assert_awaited_once_with(7, "private")


def test_delivery_failure_has_controlled_message(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); data = _data(); item, _ = _create(data); store.save(data)
    monkeypatch.setattr(handlers, "storage", store); handlers.configure_event_attachment_handlers(safe_edit_message=AsyncMock())
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    ctx = _context(event_attachment_parent=("calendar", "cal1")); ctx.bot.send_document.side_effect = BadRequest("unavailable")
    update = _callback(f"att|send|{item['id']}"); _run(handlers.event_attachment_router(update, ctx))
    assert "больше недоступен" in update.callback_query.message.reply_text.await_args.args[0]


def test_attachment_delete_confirmation_cancel_and_confirm(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); data = _data(); item, _ = _create(data); store.save(data)
    monkeypatch.setattr(handlers, "storage", store); edit = AsyncMock(); handlers.configure_event_attachment_handlers(safe_edit_message=edit)
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    ctx = _context(event_attachment_parent=("calendar", "cal1"), event_attachment_back="cal_view|vova|cal1|0")
    _run(handlers.event_attachment_router(_callback(f"att|delconfirm|{item['id']}"), ctx))
    assert edit.await_args.args[1] == "🗑 Удалить документ?"
    _run(handlers.event_attachment_router(_callback(f"att|detail|{item['id']}"), ctx))
    assert get_event_attachment(store.load(), item["id"])
    _run(handlers.event_attachment_router(_callback(f"att|delete|{item['id']}"), ctx))
    saved = store.load(); assert not get_event_attachment(saved, item["id"])
    assert saved["calendars"]["vova"][0]["id"] == "cal1"


def test_afisha_delete_cascades_once_but_status_change_preserves_files():
    data = _data(); item, _ = _create(data, "afisha", "afi1")
    apply_afisha_status_update(data, data["afisha"][0], "done")
    assert get_event_attachment(data, item["id"])
    apply_afisha_delete(data, data["afisha"][0])
    assert not get_event_attachment(data, item["id"])


def test_native_calendar_delete_warns_cancels_and_cascades(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); data = _data(); item, _ = _create(data); store.save(data)
    edit = AsyncMock(); monkeypatch.setattr(calendar_handlers, "storage", store)
    calendar_handlers.configure_calendar_handlers(safe_edit_message=edit, main_menu_keyboard=lambda: None,
        notify_other_user_about_calendar_item=AsyncMock())
    update = _callback("cal_delete_confirm|vova|cal1|0")
    _run(calendar_handlers.handle_calendar_delete_confirm(update, "vova", "cal1", 0))
    assert "К событию прикреплено 1 документов" in edit.await_args.args[1]
    # The cancel button only navigates back and therefore performs no mutation.
    cancel_callback = edit.await_args.kwargs["reply_markup"].inline_keyboard[1][0].callback_data
    assert cancel_callback == "cal_view|vova|cal1|0"
    assert get_event_attachment(store.load(), item["id"])
    _run(calendar_handlers.handle_calendar_delete(update, "vova", "cal1", 0))
    saved = store.load()
    assert not any(row["id"] == "cal1" for row in saved["calendars"]["vova"])
    assert saved["event_attachments"] == []


def test_native_calendar_delete_without_files_has_no_warning(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); store.save(_data()); edit = AsyncMock()
    monkeypatch.setattr(calendar_handlers, "storage", store)
    calendar_handlers.configure_calendar_handlers(safe_edit_message=edit, main_menu_keyboard=lambda: None,
        notify_other_user_about_calendar_item=AsyncMock())
    _run(calendar_handlers.handle_calendar_delete_confirm(_callback("cal_delete_confirm|vova|cal1|0"), "vova", "cal1", 0))
    assert "К событию прикреплено" not in edit.await_args.args[1]


def test_native_afisha_delete_confirmation_warns(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); data = _data(); _create(data, "afisha", "afi1"); store.save(data)
    edit = AsyncMock(); monkeypatch.setattr(runtime, "storage", store); monkeypatch.setattr(runtime, "safe_edit_message", edit)
    monkeypatch.setattr(runtime, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(runtime, "remember_current_chat", AsyncMock())
    _run(runtime.section_router(_callback("delete_confirm|afisha|afi1|0"), _context()))
    assert "К событию прикреплено 1 документов" in edit.await_args.args[1]
    monkeypatch.setattr(runtime, "show_list", AsyncMock(return_value=runtime.SECTION))
    _run(runtime.section_router(_callback("delete|afisha|afi1|0"), _context()))
    saved = store.load()
    assert not any(row["id"] == "afi1" for row in saved["afisha"])
    assert not any(row.get("source_id") == "afi1" for rows in saved["calendars"].values() for row in rows)
    assert saved["event_attachments"] == []


def test_attachment_operations_do_not_log_sensitive_metadata(caplog):
    data = _data()
    with caplog.at_level(logging.DEBUG):
        item, _ = _create(data); get_attachments_for_event(data, "calendar", "cal1"); delete_event_attachment(data, item["id"])
    for secret in ("secret-file-id", "unique", "ticket.pdf"):
        assert secret not in caplog.text
